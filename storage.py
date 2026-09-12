"""SQLite persistence for StageViva's first end-to-end pipeline."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from opportunity_discovery import DiscoveredOpportunity


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


class StageVivaStorage:
    """Small, portable persistence boundary; replaceable by a hosted DB later."""

    def __init__(self, database_path: str | Path) -> None:
        # FastAPI runs synchronous endpoint code in a worker thread, while the
        # dependency that opens this per-request connection may run elsewhere.
        # The connection is never shared between requests, so allowing that
        # hand-off is safe and prevents CV analysis from failing while saving.
        self.connection = sqlite3.connect(database_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS artists (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, dna_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS opportunities (
                id TEXT PRIMARY KEY, listing_url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                source_name TEXT NOT NULL, source_url TEXT NOT NULL, category TEXT NOT NULL,
                opportunity_json TEXT NOT NULL, visible INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS match_results (
                artist_id TEXT NOT NULL, opportunity_id TEXT NOT NULL, match_json TEXT NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY (artist_id, opportunity_id)
            );
            CREATE TABLE IF NOT EXISTS notification_outbox (
                id TEXT PRIMARY KEY, artist_id TEXT NOT NULL, opportunity_id TEXT NOT NULL,
                match_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL, UNIQUE (artist_id, opportunity_id)
            );
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL, display_name TEXT NOT NULL,
                profile_json TEXT NOT NULL DEFAULT '{}', artist_id TEXT,
                membership_tier TEXT NOT NULL DEFAULT 'free',
                email_notifications INTEGER NOT NULL DEFAULT 1,
                in_app_notifications INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_runs (
                id TEXT PRIMARY KEY, source_name TEXT NOT NULL, started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT NOT NULL,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS rejected_listings (
                listing_url TEXT PRIMARY KEY, source_name TEXT NOT NULL,
                reason TEXT NOT NULL, rejected_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cv_analysis_jobs (
                user_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                error TEXT, started_at TEXT NOT NULL, completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS cv_upload_events (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, endpoint TEXT NOT NULL UNIQUE,
                subscription_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS push_deliveries (
                notification_id TEXT NOT NULL, subscription_id TEXT NOT NULL,
                delivered_at TEXT NOT NULL,
                PRIMARY KEY (notification_id, subscription_id)
            );
            CREATE INDEX IF NOT EXISTS idx_cv_upload_events_user
            ON cv_upload_events (user_id);
        """)
        self._ensure_column("notification_outbox", "read_at", "TEXT")
        self._ensure_column("notification_outbox", "email_sent_at", "TEXT")
        self._ensure_column("users", "external_auth_id", "TEXT")
        # Existing beta testers keep their entitlement. New accounts are
        # explicitly created as Free below, regardless of an old SQLite column
        # default left behind by a prior deployment.
        self._ensure_column("users", "membership_tier", "TEXT NOT NULL DEFAULT 'free'")
        self._ensure_column("opportunities", "visible", "INTEGER NOT NULL DEFAULT 1")
        self.connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_external_auth_id
            ON users(external_auth_id) WHERE external_auth_id IS NOT NULL
        """)
        # A user who already analysed a CV before this limit was introduced has
        # used one upload.  This migration is idempotent and contains no CV
        # content, only the existing job timestamp.
        self.connection.execute("""
            INSERT OR IGNORE INTO cv_upload_events (id, user_id, created_at)
            SELECT 'legacy_cv_' || user_id, user_id, started_at FROM cv_analysis_jobs
            WHERE NOT EXISTS (
                SELECT 1 FROM cv_upload_events
                WHERE cv_upload_events.user_id = cv_analysis_jobs.user_id
            )
        """)
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self) -> None:
        self.connection.close()

    def opportunity_exists(self, listing_url: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM opportunities WHERE listing_url = ?", (listing_url,),
        ).fetchone() is not None

    def listing_seen(self, listing_url: str) -> bool:
        return self.opportunity_exists(listing_url) or self.connection.execute(
            "SELECT 1 FROM rejected_listings WHERE listing_url = ?", (listing_url,),
        ).fetchone() is not None

    def reject_listing(self, listing_url: str, source_name: str, reason: str) -> None:
        self.connection.execute("""
            INSERT OR IGNORE INTO rejected_listings (listing_url, source_name, reason, rejected_at)
            VALUES (?, ?, ?, ?)
        """, (listing_url, source_name, reason, _utc_now()))
        self.connection.commit()

    def hide_opportunity(self, opportunity_id: str) -> None:
        """Remove an ineligible listing from every user's feed without deleting history."""
        self.connection.execute(
            "UPDATE opportunities SET visible = 0, updated_at = ? WHERE id = ?",
            (_utc_now(), opportunity_id),
        )
        self.connection.commit()

    def set_opportunity_visibility(self, opportunity_id: str, visible: bool) -> bool:
        cursor = self.connection.execute("""
            UPDATE opportunities SET visible = ?, updated_at = ? WHERE id = ?
        """, (int(visible), _utc_now(), opportunity_id))
        self.connection.commit()
        return cursor.rowcount == 1

    def update_opportunity_title(self, opportunity_id: str, title: str) -> bool:
        row = self.connection.execute(
            "SELECT opportunity_json FROM opportunities WHERE id = ?", (opportunity_id,),
        ).fetchone()
        if not row:
            return False
        opportunity = json.loads(row["opportunity_json"])
        identity = opportunity.setdefault("identity", {})
        title_field = identity.get("title")
        if isinstance(title_field, dict):
            title_field["value"] = title
        else:
            identity["title"] = {"value": title}
        self.connection.execute("""
            UPDATE opportunities SET title = ?, opportunity_json = ?, updated_at = ? WHERE id = ?
        """, (title, json.dumps(opportunity, ensure_ascii=False), _utc_now(), opportunity_id))
        self.connection.commit()
        return True

    def update_opportunity_details(self, opportunity_id: str, updates: dict[str, str]) -> dict[str, Any] | None:
        """Update the editable editorial fields while retaining raw import history."""
        row = self.connection.execute(
            "SELECT opportunity_json FROM opportunities WHERE id = ?", (opportunity_id,),
        ).fetchone()
        if not row:
            return None
        opportunity = json.loads(row["opportunity_json"])

        def set_value(path: tuple[str, ...], value: str) -> None:
            target: dict[str, Any] = opportunity
            for key in path[:-1]:
                child = target.get(key)
                if not isinstance(child, dict):
                    child = {}
                    target[key] = child
                target = child
            field = target.get(path[-1])
            if isinstance(field, dict):
                field["value"] = value
            else:
                target[path[-1]] = {"value": value}

        paths = {
            "title": ("identity", "title"),
            "organisation": ("identity", "organisation"),
            "role_summary": ("identity", "opportunity_type"),
            "description": ("identity", "description"),
            "location": ("location", "city"),
            "deadline": ("dates", "application_deadline"),
            "contract_type": ("contract_and_compensation", "contract_type"),
            "official_url": ("application", "application_url"),
        }
        for key, value in updates.items():
            if key in paths:
                set_value(paths[key], value)
        if "official_url" in updates:
            set_value(("source", "official_url"), updates["official_url"])
        title = updates.get("title") or str(
            opportunity.get("identity", {}).get("title", {}).get("value") or "Untitled opportunity"
        )
        self.connection.execute("""
            UPDATE opportunities SET title = ?, opportunity_json = ?, updated_at = ? WHERE id = ?
        """, (title, json.dumps(opportunity, ensure_ascii=False), _utc_now(), opportunity_id))
        self.connection.commit()
        return opportunity

    def list_all_opportunities(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("""
            SELECT id, listing_url, title, source_name, source_url, category, visible,
                   opportunity_json, created_at, updated_at
            FROM opportunities ORDER BY updated_at DESC
        """).fetchall()
        return [{**dict(row), "visible": bool(row["visible"]),
                 "opportunity": json.loads(row["opportunity_json"])} for row in rows]

    def upsert_artist(self, artist: dict[str, Any]) -> str:
        name = str(artist.get("identity", {}).get("name") or "unknown artist")
        artist_id = _stable_id("artist", name.lower())
        self.connection.execute("""
            INSERT INTO artists (id, name, dna_json, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, dna_json=excluded.dna_json,
                updated_at=excluded.updated_at
        """, (artist_id, name, json.dumps(artist, ensure_ascii=False), _utc_now()))
        self.connection.commit()
        return artist_id

    def upsert_artist_for_user(self, user_id: str, artist: dict[str, Any]) -> str:
        """Store a user's Artist DNA without relying on their display name."""
        name = str(artist.get("identity", {}).get("name") or "unknown artist")
        artist_id = _stable_id("artist", user_id)
        self.connection.execute("""
            INSERT INTO artists (id, name, dna_json, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, dna_json=excluded.dna_json,
                updated_at=excluded.updated_at
        """, (artist_id, name, json.dumps(artist, ensure_ascii=False), _utc_now()))
        self.connection.execute(
            "UPDATE users SET artist_id = ?, updated_at = ? WHERE id = ?",
            (artist_id, _utc_now(), user_id),
        )
        self.connection.commit()
        return artist_id

    def create_user(self, email: str, password_hash: str, display_name: str) -> dict[str, Any]:
        user_id = _stable_id("user", email.lower())
        now = _utc_now()
        try:
            self.connection.execute("""
                INSERT INTO users (id, email, password_hash, display_name, membership_tier, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'free', ?, ?)
            """, (user_id, email.lower(), password_hash, display_name, now, now))
            self.connection.commit()
        except sqlite3.IntegrityError as error:
            raise ValueError("An account with this email already exists.") from error
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._user_from_row(row) if row else None

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
        return self._user_from_row(row) if row else None

    def get_or_create_external_user(
        self, external_auth_id: str, email: str | None, display_name: str | None,
    ) -> dict[str, Any]:
        """Link a verified Lovable Cloud identity to one StageViva user record."""
        row = self.connection.execute(
            "SELECT * FROM users WHERE external_auth_id = ?", (external_auth_id,),
        ).fetchone()
        if row:
            user = self._user_from_row(row)
            changed_email = str(email or user["email"]).lower()
            changed_name = str(display_name or user["display_name"]).strip() or user["display_name"]
            if changed_email != user["email"] or changed_name != user["display_name"]:
                self.connection.execute("""
                    UPDATE users SET email = ?, display_name = ?, updated_at = ? WHERE id = ?
                """, (changed_email, changed_name, _utc_now(), user["id"]))
                self.connection.commit()
                return self.get_user(user["id"])  # type: ignore[return-value]
            return user

        # If the user previously used the local development login with this email,
        # preserve their existing Artist DNA and connect that account instead.
        existing = self.get_user_by_email(email) if email else None
        if existing:
            self.connection.execute("""
                UPDATE users SET external_auth_id = ?, updated_at = ? WHERE id = ?
            """, (external_auth_id, _utc_now(), existing["id"]))
            self.connection.commit()
            return self.get_user(existing["id"])  # type: ignore[return-value]

        user_id = _stable_id("external-user", external_auth_id)
        safe_email = str(email or f"{external_auth_id}@lovable-auth.invalid").lower()
        safe_name = str(display_name or "StageViva performer").strip() or "StageViva performer"
        now = _utc_now()
        self.connection.execute("""
            INSERT INTO users (
                id, email, password_hash, display_name, external_auth_id, membership_tier, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'free', ?, ?)
        """, (user_id, safe_email, "external-auth-managed", safe_name, external_auth_id, now, now))
        self.connection.commit()
        return self.get_user(user_id)  # type: ignore[return-value]

    @staticmethod
    def _user_from_row(row: sqlite3.Row) -> dict[str, Any]:
        user = dict(row)
        user["profile"] = json.loads(user.pop("profile_json"))
        user["email_notifications"] = bool(user["email_notifications"])
        user["in_app_notifications"] = bool(user["in_app_notifications"])
        return user

    def update_user_profile(self, user_id: str, display_name: str, profile: dict[str, Any]) -> dict[str, Any]:
        self.connection.execute("""
            UPDATE users SET display_name = ?, profile_json = ?, updated_at = ? WHERE id = ?
        """, (display_name, json.dumps(profile, ensure_ascii=False), _utc_now(), user_id))
        self.connection.commit()
        return self.get_user(user_id)  # type: ignore[return-value]

    def update_notification_preferences(self, user_id: str, *, email: bool, in_app: bool) -> dict[str, Any]:
        self.connection.execute("""
            UPDATE users SET email_notifications = ?, in_app_notifications = ?, updated_at = ?
            WHERE id = ?
        """, (int(email), int(in_app), _utc_now(), user_id))
        self.connection.commit()
        return self.get_user(user_id)  # type: ignore[return-value]

    def update_membership_tier(self, user_id: str, membership_tier: str) -> dict[str, Any] | None:
        """Internal billing boundary; Stripe/App Store wiring will call this later."""
        self.connection.execute("""
            UPDATE users SET membership_tier = ?, updated_at = ? WHERE id = ?
        """, (membership_tier, _utc_now(), user_id))
        self.connection.commit()
        return self.get_user(user_id)

    def list_performers_for_admin(self) -> list[dict[str, Any]]:
        """Return the creator's private profile overview without account secrets or CV files."""
        rows = self.connection.execute("""
            SELECT users.id, users.email, users.display_name, users.profile_json,
                   users.membership_tier, users.artist_id, users.created_at, users.updated_at,
                   artists.dna_json
            FROM users LEFT JOIN artists ON artists.id = users.artist_id
            ORDER BY users.updated_at DESC
        """).fetchall()
        performers: list[dict[str, Any]] = []
        for row in rows:
            performer = dict(row)
            performer["profile"] = json.loads(performer.pop("profile_json"))
            raw_dna = performer.pop("dna_json")
            performer["artist_dna"] = json.loads(raw_dna) if raw_dna else None
            performers.append(performer)
        return performers

    def start_cv_analysis(self, user_id: str) -> dict[str, Any]:
        now = _utc_now()
        self.connection.execute("""
            INSERT INTO cv_analysis_jobs (user_id, status, error, started_at, completed_at)
            VALUES (?, 'processing', NULL, ?, NULL)
            ON CONFLICT(user_id) DO UPDATE SET status='processing', error=NULL,
                started_at=excluded.started_at, completed_at=NULL
        """, (user_id, now))
        self.connection.commit()
        return self.get_cv_analysis(user_id)  # type: ignore[return-value]

    def cv_upload_count(self, user_id: str) -> int:
        return int(self.connection.execute(
            "SELECT COUNT(*) FROM cv_upload_events WHERE user_id = ?", (user_id,),
        ).fetchone()[0])

    def reserve_cv_upload(self, user_id: str, *, maximum: int = 2) -> bool:
        """Atomically reserve one of a performer's limited CV analyses."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            if self.cv_upload_count(user_id) >= maximum:
                self.connection.rollback()
                return False
            now = _utc_now()
            self.connection.execute(
                "INSERT INTO cv_upload_events (id, user_id, created_at) VALUES (?, ?, ?)",
                (_stable_id("cv_upload", f"{user_id}:{now}:{uuid.uuid4().hex}"), user_id, now),
            )
            self.connection.commit()
            return True
        except Exception:
            self.connection.rollback()
            raise

    def complete_cv_analysis(self, user_id: str, error: str | None = None) -> None:
        self.connection.execute("""
            UPDATE cv_analysis_jobs SET status = ?, error = ?, completed_at = ? WHERE user_id = ?
        """, ("failed" if error else "complete", error, _utc_now(), user_id))
        self.connection.commit()

    def get_cv_analysis(self, user_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT status, error, started_at, completed_at FROM cv_analysis_jobs WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_processing_cv_analyses(self) -> list[str]:
        rows = self.connection.execute(
            "SELECT user_id FROM cv_analysis_jobs WHERE status = 'processing'",
        ).fetchall()
        return [str(row["user_id"]) for row in rows]

    def get_artist_for_user(self, user_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("""
            SELECT artists.id, artists.name, artists.dna_json, artists.updated_at
            FROM users JOIN artists ON artists.id = users.artist_id WHERE users.id = ?
        """, (user_id,)).fetchone()
        if not row:
            return None
        artist = dict(row)
        artist["dna"] = json.loads(artist.pop("dna_json"))
        return artist

    def list_registered_artists(self) -> list[tuple[str, dict[str, Any]]]:
        rows = self.connection.execute("""
            SELECT artists.id, artists.dna_json FROM users
            JOIN artists ON artists.id = users.artist_id
        """).fetchall()
        return [(row["id"], json.loads(row["dna_json"])) for row in rows]

    def list_opportunity_dnas(self) -> list[tuple[str, dict[str, Any]]]:
        rows = self.connection.execute(
            "SELECT id, opportunity_json FROM opportunities WHERE visible = 1",
        ).fetchall()
        return [(row["id"], json.loads(row["opportunity_json"])) for row in rows]

    def list_matches_for_user(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("""
            SELECT match_results.opportunity_id, match_results.match_json, match_results.updated_at,
                   opportunities.title, opportunities.listing_url, opportunities.opportunity_json
            FROM users
            JOIN match_results ON match_results.artist_id = users.artist_id
            JOIN opportunities ON opportunities.id = match_results.opportunity_id
            WHERE users.id = ? AND opportunities.visible = 1 ORDER BY match_results.updated_at DESC
        """, (user_id,)).fetchall()
        matches = [{
            "opportunity_id": row["opportunity_id"], "title": row["title"],
            "listing_url": row["listing_url"], "match": json.loads(row["match_json"]),
            "opportunity": json.loads(row["opportunity_json"]), "updated_at": row["updated_at"],
        } for row in rows]
        # The dashboard is a recommendation feed, not an import history.
        # Return the strongest relevant opportunity first, falling back to the
        # most recently updated item only when scores are identical or absent.
        matches.sort(
            key=lambda item: (
                int(item["match"].get("overall", {}).get("match_score", 0) or 0),
                item["updated_at"],
            ),
            reverse=True,
        )
        return matches

    def list_notifications_for_user(self, user_id: str) -> list[dict[str, Any]]:
        preferences = self.connection.execute(
            "SELECT in_app_notifications FROM users WHERE id = ?", (user_id,),
        ).fetchone()
        if not preferences or not preferences["in_app_notifications"]:
            return []
        rows = self.connection.execute("""
            SELECT notification_outbox.id, notification_outbox.status, notification_outbox.created_at,
                   notification_outbox.read_at, notification_outbox.match_json,
                   opportunities.id AS opportunity_id, opportunities.title, opportunities.listing_url
            FROM users
            JOIN notification_outbox ON notification_outbox.artist_id = users.artist_id
            JOIN opportunities ON opportunities.id = notification_outbox.opportunity_id
            WHERE users.id = ? ORDER BY notification_outbox.created_at DESC
        """, (user_id,)).fetchall()
        return [{**dict(row), "match": json.loads(row["match_json"])} for row in rows]

    def pending_email_notifications(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute("""
            SELECT notification_outbox.id, users.email, users.display_name,
                   opportunities.title, opportunities.listing_url, notification_outbox.match_json
            FROM notification_outbox
            JOIN users ON users.artist_id = notification_outbox.artist_id
            JOIN opportunities ON opportunities.id = notification_outbox.opportunity_id
            WHERE users.email_notifications = 1 AND notification_outbox.email_sent_at IS NULL
            ORDER BY notification_outbox.created_at ASC LIMIT ?
        """, (limit,)).fetchall()
        return [{**dict(row), "match": json.loads(row["match_json"])} for row in rows]

    def mark_notification_email_sent(self, notification_id: str) -> None:
        self.connection.execute("""
            UPDATE notification_outbox SET email_sent_at = ?, status = 'email_sent' WHERE id = ?
        """, (_utc_now(), notification_id))
        self.connection.commit()

    def mark_notification_read(self, user_id: str, notification_id: str) -> bool:
        cursor = self.connection.execute("""
            UPDATE notification_outbox SET read_at = ?
            WHERE id = ? AND artist_id = (SELECT artist_id FROM users WHERE id = ?)
        """, (_utc_now(), notification_id, user_id))
        self.connection.commit()
        return cursor.rowcount == 1

    def upsert_push_subscription(self, user_id: str, subscription: dict[str, Any]) -> None:
        endpoint = str(subscription.get("endpoint") or "")
        if not endpoint:
            raise ValueError("A push subscription needs an endpoint.")
        subscription_id = _stable_id("push", endpoint)
        now = _utc_now()
        self.connection.execute("""
            INSERT INTO push_subscriptions (id, user_id, endpoint, subscription_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id,
                subscription_json=excluded.subscription_json, updated_at=excluded.updated_at
        """, (subscription_id, user_id, endpoint, json.dumps(subscription), now, now))
        self.connection.commit()

    def remove_push_subscription(self, user_id: str, endpoint: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?", (user_id, endpoint),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def remove_push_subscription_by_id(self, subscription_id: str) -> bool:
        cursor = self.connection.execute("DELETE FROM push_subscriptions WHERE id = ?", (subscription_id,))
        self.connection.commit()
        return cursor.rowcount == 1

    def list_push_subscriptions_for_user(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("""
            SELECT id, subscription_json FROM push_subscriptions WHERE user_id = ?
        """, (user_id,)).fetchall()
        return [{"id": row["id"], "subscription": json.loads(row["subscription_json"])} for row in rows]

    def pending_push_notifications(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute("""
            SELECT notification_outbox.id AS notification_id, notification_outbox.match_json,
                   opportunities.id AS opportunity_id, opportunities.title,
                   push_subscriptions.id AS subscription_id, push_subscriptions.endpoint,
                   push_subscriptions.subscription_json
            FROM notification_outbox
            JOIN users ON users.artist_id = notification_outbox.artist_id
            JOIN opportunities ON opportunities.id = notification_outbox.opportunity_id
            JOIN push_subscriptions ON push_subscriptions.user_id = users.id
            LEFT JOIN push_deliveries ON push_deliveries.notification_id = notification_outbox.id
                AND push_deliveries.subscription_id = push_subscriptions.id
            WHERE users.in_app_notifications = 1
              AND push_deliveries.notification_id IS NULL
              AND notification_outbox.created_at >= push_subscriptions.created_at
            ORDER BY notification_outbox.created_at ASC LIMIT ?
        """, (limit,)).fetchall()
        return [{**dict(row), "match": json.loads(row["match_json"]),
                 "subscription": json.loads(row["subscription_json"])} for row in rows]

    def mark_push_delivered(self, notification_id: str, subscription_id: str) -> None:
        self.connection.execute("""
            INSERT OR IGNORE INTO push_deliveries (notification_id, subscription_id, delivered_at)
            VALUES (?, ?, ?)
        """, (notification_id, subscription_id, _utc_now()))
        self.connection.commit()

    def get_or_create_setting(self, key: str, factory: Callable[[], str]) -> str:
        row = self.connection.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        if row:
            return str(row["value"])
        value = str(factory())
        self.connection.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, value))
        self.connection.commit()
        return value

    def record_source_run(
        self, source_name: str, started_at: str, result: dict[str, Any], *, error: str | None = None,
    ) -> str:
        run_id = _stable_id("source_run", f"{source_name}:{started_at}")
        self.connection.execute("""
            INSERT INTO source_runs (id, source_name, started_at, completed_at, status, result_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id, source_name, started_at, _utc_now(), "failed" if error else "completed",
            json.dumps(result, ensure_ascii=False), error,
        ))
        self.connection.commit()
        return run_id

    def list_source_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute("""
            SELECT * FROM source_runs ORDER BY rowid DESC LIMIT ?
        """, (limit,)).fetchall()
        return [{**dict(row), "result": json.loads(row["result_json"])} for row in rows]

    def upsert_opportunity(self, item: DiscoveredOpportunity, opportunity: dict[str, Any]) -> str:
        opportunity_id = _stable_id("opportunity", item.listing_url.rstrip("/").lower())
        now = _utc_now()
        self.connection.execute("""
            INSERT INTO opportunities (id, listing_url, title, source_name, source_url, category,
                opportunity_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(listing_url) DO UPDATE SET title=excluded.title,
                source_name=excluded.source_name, source_url=excluded.source_url,
                category=excluded.category, opportunity_json=excluded.opportunity_json,
                updated_at=excluded.updated_at
        """, (opportunity_id, item.listing_url, item.title, item.source_name, item.source_url,
              item.category, json.dumps(opportunity, ensure_ascii=False), now, now))
        self.connection.commit()
        return opportunity_id

    def upsert_match(self, artist_id: str, opportunity_id: str, match: dict[str, Any]) -> None:
        self.connection.execute("""
            INSERT INTO match_results (artist_id, opportunity_id, match_json, updated_at)
            VALUES (?, ?, ?, ?) ON CONFLICT(artist_id, opportunity_id) DO UPDATE SET
                match_json=excluded.match_json, updated_at=excluded.updated_at
        """, (artist_id, opportunity_id, json.dumps(match, ensure_ascii=False), _utc_now()))
        self.connection.commit()

    def queue_notification(self, artist_id: str, opportunity_id: str, match: dict[str, Any]) -> bool:
        preferences = self.connection.execute("""
            SELECT email_notifications, in_app_notifications FROM users WHERE artist_id = ?
        """, (artist_id,)).fetchone()
        if preferences and not (preferences["email_notifications"] or preferences["in_app_notifications"]):
            return False
        notification_id = _stable_id("notification", f"{artist_id}:{opportunity_id}")
        cursor = self.connection.execute("""
            INSERT OR IGNORE INTO notification_outbox (id, artist_id, opportunity_id, match_json, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (notification_id, artist_id, opportunity_id, json.dumps(match, ensure_ascii=False), _utc_now()))
        self.connection.commit()
        return cursor.rowcount == 1

    def count(self, table: str) -> int:
        if table not in {"artists", "opportunities", "match_results", "notification_outbox"}:
            raise ValueError("Unknown StageViva table.")
        return int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
