"""Private, durable owner-supplied opportunity inbox. Never scrapes Instagram."""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from PIL import Image

from ai_safety import AIRequestsDisabledError
from opportunity_screenshots import read_screenshot as _extract_image_text
from opportunity_discovery import DiscoveredOpportunity
from opportunity_intelligence import (
    DEFAULT_HEADERS, analyse_opportunity, extract_official_application_url, extract_useful_text,
)
from opportunity_quality import VALID, application_pathway, review_reason, value_at, unwrap_source_urls
from opportunity_source_facts import complete_source_facts
from opportunity_publication import PUBLICATION_POLICY, prepare_publication, publication_reason
from public_source import PublicSourceError, fetch_public_html, public_url

ACTIVE = ("queued", "reading", "analysing", "publishing")
RETRYABLE = ("needs_information", "failed", "paused", "interrupted", "review")
MAX_IMAGES = 5
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
UPLOAD_PREFIX = "stageviva-upload://"
SOURCE_READER_VERSION = "screenshot-bands-v2"


def import_failure(error: Exception) -> tuple[str, str]:
    """Safe machine diagnostics; never persist provider bodies or signed URLs."""
    status = getattr(error, "status_code", None)
    body = getattr(error, "body", None)
    code = body.get("code") if isinstance(body, dict) else None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        code = body["error"].get("code", code)
    if code == "invalid_json_schema":
        return "analysis_format_error", "The analysis service rejected the request format. Your input is saved; wait for an importer update before retrying."
    if status in {401, 403}:
        return "analysis_access_error", "The analysis service could not be accessed. Your input is saved; the server configuration needs checking."
    if status == 429:
        return "analysis_rate_limited", "The analysis service is temporarily unavailable or its usage limit was reached. Your input is saved."
    if "timeout" in type(error).__name__.lower():
        return "analysis_timeout", "The analysis service did not finish in time. Your input is saved."
    if status == 400:
        return "analysis_request_rejected", "The analysis service rejected this request. Your input is saved; the importer needs checking."
    if type(error) is RuntimeError and str(error) in {"Opportunity extraction did not match the required schema.", "Opportunity extraction returned invalid JSON."}:
        return "analysis_response_format", "The analysis response could not be read. Your input is saved."
    return "processing_error", "The import could not finish. Your input is saved. The failure reference below can help identify the cause."


def screenshot_source(url: str) -> bool:
    return url.startswith(UPLOAD_PREFIX)


def _record_hash(opportunity: dict) -> str:
    snapshot = json.loads(json.dumps(opportunity))
    review = snapshot.get("stageviva_review", {})
    review.pop("review_policy", None)
    review.pop("catalogue_snapshot_hash", None)
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


class ImportInputError(ValueError):
    pass


def instagram_url(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in {"instagram.com", "www.instagram.com", "m.instagram.com"}


def canonical_source(url: str) -> str:
    url = url.strip()
    if len(url) > 2048 or not public_url(url):
        raise ImportInputError("Use a public HTTPS or HTTP post or listing link.")
    parsed = urlsplit(url)
    if instagram_url(url):
        match = re.fullmatch(r"/(p|reel|tv)/([A-Za-z0-9_-]+)/?", parsed.path)
        if not match:
            raise ImportInputError("Use an individual Instagram post or reel link, rather than a profile or story.")
        return f"https://www.instagram.com/{match[1]}/{match[2]}/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def validate_image(data: bytes) -> str:
    if len(data) > MAX_IMAGE_BYTES:
        raise ImportInputError("Each screenshot must be smaller than 8 MB.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError()
            width, height = image.size
            if min(width, height) < 200 or width * height > 40_000_000:
                raise ValueError()
            extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[image.format]
            image.verify()
            return extension
    except Exception as error:
        raise ImportInputError("Use a readable PNG, JPG or WebP screenshot, at least 200 pixels on each side.") from error


def research_links(text: str) -> list[str]:
    links = []
    # Posters often omit https://. Only infer it for an explicitly labelled
    # website/application domain, never for an email address or filename.
    text = re.sub(r"(\b(?:website|official\s+site|apply\s+(?:at|via))\s*:?\s*)(?!https?://|www\.)([A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,63}(?:/[^\s<>\"']*)?)", r"\1https://\2", text, flags=re.I)
    text = unwrap_source_urls(text)
    for match in re.finditer(r"(?:https?://|www\.)[^\s<>\"']+", text):
        url = match.group().rstrip(".,;:!?)\u201d\u2019")
        if url.startswith("www."):
            url = "https://" + url
        host = (urlsplit(url).hostname or "").lower()
        if public_url(url) and not instagram_url(url) and host not in {"facebook.com", "www.facebook.com", "fb.com", "www.fb.com"}:
            if url not in links:
                links.append(url)
    # Prefer application links to incidental social/contact links; bound research.
    return sorted(links, key=lambda url: not bool(re.search(r"apply|audition|casting|form|careers", url, re.I)))[:2]


def html_research_links(html: str, base_url: str) -> list[str]:
    """Keep application anchor destinations, which plain-text extraction drops."""
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["nav", "footer", "header", "aside", "script", "style"]):
        element.decompose()
    candidates = []
    for anchor in soup.find_all("a", href=True):
        url = urljoin(base_url, str(anchor["href"]).strip())
        label = anchor.get_text(" ", strip=True)
        if url == base_url or not re.search(r"\b(apply(?:ing)?|applications?|audition|casting|official announcement|vacancy)\b", label, re.I):
            continue
        if research_links(url) and url not in candidates:
            candidates.append(url)
    return candidates[:2]


class ImportQueue:
    def __init__(self, storage, root: Path):
        self.storage = storage
        self.db = storage.connection
        self.root = root
        self.db.execute("""CREATE TABLE IF NOT EXISTS opportunity_imports (
            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, request_id TEXT NOT NULL,
            input_hash TEXT NOT NULL, source_url TEXT NOT NULL, caption TEXT NOT NULL,
            reference_url TEXT NOT NULL DEFAULT '',
            source_complete INTEGER NOT NULL, images_json TEXT NOT NULL,
            status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', source_text TEXT,
            source_kind TEXT NOT NULL DEFAULT 'caption', research_json TEXT NOT NULL DEFAULT '[]',
            opportunity_json TEXT, opportunity_id TEXT, matching_status TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            UNIQUE(owner_id, request_id))""")
        # Existing saved imports survive this additive, restart-safe migration.
        if "reference_url" not in {row[1] for row in self.db.execute("PRAGMA table_info(opportunity_imports)")}:
            try:
                self.db.execute("ALTER TABLE opportunity_imports ADD COLUMN reference_url TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                if "reference_url" not in {row[1] for row in self.db.execute("PRAGMA table_info(opportunity_imports)")}:
                    raise
        self.db.execute("CREATE INDEX IF NOT EXISTS opportunity_import_work ON opportunity_imports(status,created_at)")
        if "replacement_hash" not in {row[1] for row in self.db.execute("PRAGMA table_info(opportunity_imports)")}:
            try:
                self.db.execute("ALTER TABLE opportunity_imports ADD COLUMN replacement_hash TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                if "replacement_hash" not in {row[1] for row in self.db.execute("PRAGMA table_info(opportunity_imports)")}:
                    raise
        self.db.execute("CREATE INDEX IF NOT EXISTS opportunity_import_source ON opportunity_imports(source_url,status)")
        if "source_reader_version" not in {row[1] for row in self.db.execute("PRAGMA table_info(opportunity_imports)")}:
            try:
                self.db.execute("ALTER TABLE opportunity_imports ADD COLUMN source_reader_version TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                if "source_reader_version" not in {row[1] for row in self.db.execute("PRAGMA table_info(opportunity_imports)")}:
                    raise
        self.db.commit()

        for column in ("failure_code", "failure_stage", "failure_type"):
            if column not in {row[1] for row in self.db.execute("PRAGMA table_info(opportunity_imports)")}:
                try:
                    self.db.execute(f"ALTER TABLE opportunity_imports ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
                except sqlite3.OperationalError:
                    if column not in {row[1] for row in self.db.execute("PRAGMA table_info(opportunity_imports)")}:
                        raise
        self.db.commit()

        self.db.execute("""CREATE TABLE IF NOT EXISTS catalogue_match_jobs (
            opportunity_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
            generation TEXT NOT NULL, status TEXT NOT NULL, updated_at REAL NOT NULL)""")
        self.db.commit()

        if "notification_read_at" not in {row[1] for row in self.db.execute("PRAGMA table_info(opportunity_imports)")}:
            try:
                self.db.execute("ALTER TABLE opportunity_imports ADD COLUMN notification_read_at TEXT")
            except sqlite3.OperationalError:
                if "notification_read_at" not in {row[1] for row in self.db.execute("PRAGMA table_info(opportunity_imports)")}:
                    raise
        self.db.commit()

    def queue_saved_match(self, opportunity_id: str, owner_id: str):
        """Persist rematching separately from a completed admin save."""
        self.db.execute("""INSERT INTO catalogue_match_jobs VALUES (?,?,?,'pending',?)
            ON CONFLICT(opportunity_id) DO UPDATE SET owner_id=excluded.owner_id,
            generation=excluded.generation,status='pending',updated_at=excluded.updated_at""",
            (opportunity_id, owner_id, uuid.uuid4().hex, time.time()))
        self.db.commit()

    def match_saved_one(self, authorised, matcher):
        self.db.execute("BEGIN IMMEDIATE")
        row = self.db.execute("""SELECT * FROM catalogue_match_jobs
            WHERE status='pending' OR (status='running' AND updated_at<?)
            ORDER BY updated_at LIMIT 1""", (time.time() - 300,)).fetchone()
        if row:
            self.db.execute("UPDATE catalogue_match_jobs SET status='running',updated_at=? WHERE opportunity_id=?",
                (time.time(), row["opportunity_id"]))
        self.db.commit()
        if not row:
            return False
        state = "complete"
        try:
            current = self.storage.get_opportunity_for_admin(row["opportunity_id"])
            if current and current["visible"] and authorised(row["owner_id"]):
                result = matcher(current["id"], current["opportunity"], self.storage)
                if result.get("remaining"):
                    state = "pending"
        except Exception:
            state = "paused"
        # An edit queued during matching must keep its newer work pending.
        self.db.execute("""UPDATE catalogue_match_jobs SET status=?,updated_at=?
            WHERE opportunity_id=? AND generation=?""",
            (state, time.time(), row["opportunity_id"], row["generation"]))
        self.db.commit()
        return True

    def notifications(self, owner_id: str):
        rows = self.db.execute("""SELECT * FROM opportunity_imports WHERE owner_id=?
            AND status IN ('published','review','duplicate','failed','paused','interrupted','needs_information')
            ORDER BY updated_at DESC LIMIT 30""", (owner_id,)).fetchall()
        results = []
        for row in rows:
            job = self.public(dict(row))
            published = job["status"] == "published"
            title = job["title"] if job["title"] and job["title"] != "unknown" else "Opportunity"
            results.append({"id": row["id"], "opportunity_id": job["opportunity_id"],
                "title": f"{title} is live" if published else f"{title}: {job['status'].replace('_', ' ')}",
                "body": "Added to StageViva. Matches may still be preparing." if published else job["message"],
                "status": job["status"], "read_at": row["notification_read_at"],
                "created_at": datetime.fromtimestamp(row["updated_at"], timezone.utc).isoformat()})
        return results

    def mark_notification_read(self, owner_id: str, job_id: str) -> bool:
        result = self.db.execute("UPDATE opportunity_imports SET notification_read_at=? WHERE id=? AND owner_id=?",
            (datetime.now(timezone.utc).isoformat(), job_id, owner_id))
        self.db.commit()
        return result.rowcount == 1

    def get(self, job_id: str, owner_id: str | None = None):
        row = self.db.execute("SELECT * FROM opportunity_imports WHERE id=?" + (" AND owner_id=?" if owner_id else ""),
                              (job_id, owner_id) if owner_id else (job_id,)).fetchone()
        return dict(row) if row else None

    def update(self, job_id: str, **values):
        allowed = {"status", "message", "source_text", "source_kind", "research_json", "reference_url", "opportunity_json", "opportunity_id", "matching_status", "caption", "source_complete", "replacement_hash", "source_reader_version", "failure_code", "failure_stage", "failure_type"}
        if not values.keys() <= allowed:
            raise ValueError("Unsupported import update")
        values["updated_at"] = time.time()
        self.db.execute("UPDATE opportunity_imports SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?",
                        (*values.values(), job_id))
        self.db.commit()

    @staticmethod
    def public(job):
        opportunity = complete_source_facts(json.loads(job["opportunity_json"] or "{}"))
        notes = []
        review = opportunity.get("stageviva_review", {})
        if job["status"] == "review":
            for key, label in (("opportunity_type", "opportunity type"), ("description", "description")):
                if str(value_at(opportunity, "identity", key) or "").strip().casefold() in {"", "unknown", "not specified", "n/a"}:
                    notes.append(f"A clear {label} could not be produced from this material. Add the full announcement or a clearer screenshot, then reanalyse.")
            for key, label in (("title", "job title"), ("organisation", "employer name")):
                if key == "organisation" and review.get("required_content_version") == "complete-opportunity-v1":
                    continue
                if str(value_at(opportunity, "identity", key) or "").strip().casefold() in {"", "unknown", "not specified", "n/a"}:
                    notes.append(f"The {label} was not captured. Add the missing announcement text below and reanalyse.")
            if not application_pathway(opportunity):
                notes.append("Add the official website/application link below, or the application email and instructions, then reanalyse.")
            if review.get("decision") != VALID:
                notes.append("The dancer role needs confirmation against the source.")
            if review.get("conflicts"):
                notes.append("Conflicting source details need checking.")
            if review.get("research_error") and review_reason(opportunity) == "unverified_application":
                notes.append("A linked website could not be read or verified.")
            if review_reason(opportunity) == "unverified_application":
                notes.append("No application method or official website was verified.")
            if review.get("blocking_reasons"):
                notes.append("Missing information: " + ", ".join(value.replace("_", " ") for value in review["blocking_reasons"]) + ".")
            if publication_reason(opportunity) == "needs_information" and not review.get("blocking_reasons"):
                notes.append(f"Extraction confidence is {opportunity.get('overall_confidence', 'unknown').replace('_', ' ')}.")
        return {key: job[key] for key in ("id", "source_url", "status", "message", "opportunity_id", "matching_status", "created_at", "updated_at")} | {
            "message": (job["message"] + " " + " ".join(notes)).strip(),
            "source_url": "" if screenshot_source(job["source_url"]) else job["source_url"],
            "reference_url": job["reference_url"],
            "title": value_at(opportunity, "identity", "title") or "",
            "caption": job["caption"], "source_complete": bool(job["source_complete"]),
            "screenshot_count": len(json.loads(job["images_json"])),
            "failure_code": job.get("failure_code", ""), "failure_stage": job.get("failure_stage", ""), "failure_type": job.get("failure_type", ""),
        }

    def list(self, owner_id: str):
        results = []
        for row in self.db.execute("SELECT * FROM opportunity_imports WHERE owner_id=? ORDER BY created_at DESC LIMIT 100", (owner_id,)).fetchall():
            job = dict(row)
            current = self.storage.opportunity_by_listing_url(job["source_url"]) if job["opportunity_id"] else None
            if current and job["status"] == "review" and current["visible"]:
                job.update(status="published", message="Published after review.", matching_status="")
            elif current and job["status"] == "published" and not current["visible"]:
                job.update(status="review", message="Hidden after review. Open the saved opportunity to check it.", matching_status="")
            results.append(self.public(job))
        return results

    def enqueue(self, owner_id: str, request_id: str, url: str, caption: str, complete: bool, images: list[bytes], reference_url: str = ""):
        url = canonical_source(url) if url.strip() else ""
        reference_url = canonical_source(reference_url) if reference_url.strip() else ""
        if not url and not images:
            raise ImportInputError("Add at least one screenshot or a post/listing link.")
        caption = caption.strip()
        if len(caption) > 20_000 or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", request_id):
            raise ImportInputError("Use a request ID of 8–100 letters/numbers and a caption under 20,000 characters.")
        if len(images) > MAX_IMAGES or sum(map(len, images)) > MAX_TOTAL_BYTES:
            raise ImportInputError("Attach up to five screenshots, totalling no more than 20 MB.")
        extensions = [validate_image(data) for data in images]
        image_hashes = [hashlib.sha256(data).hexdigest() for data in images]
        if not url:
            # Private identity, never an application link. Reordered identical
            # groups can be recognised before OCR or another paid analysis.
            url = UPLOAD_PREFIX + hashlib.sha256("\n".join(sorted(set(image_hashes))).encode()).hexdigest()
        fingerprint_values = [url, caption, complete, image_hashes]
        # Preserve idempotency keys created by the earlier URL-based client.
        if reference_url:
            fingerprint_values.append(reference_url)
        fingerprint = hashlib.sha256(json.dumps(fingerprint_values).encode()).hexdigest()
        existing = self.db.execute("SELECT * FROM opportunity_imports WHERE owner_id=? AND request_id=?", (owner_id, request_id)).fetchone()
        if existing:
            if existing["input_hash"] != fingerprint:
                raise ImportInputError("This request ID was already used for different content. Prepare a new import.")
            return self.public(dict(existing))
        job_id = uuid.uuid4().hex
        directory = self.root / job_id
        # Files exist before the row becomes claimable. No user-controlled paths.
        try:
            if images:
                directory.mkdir(parents=True, exist_ok=False)
            paths = []
            for index, (data, extension) in enumerate(zip(images, extensions)):
                path = directory / f"{index}{extension}"
                path.write_bytes(data)
                paths.append(str(path.resolve()))
            self.db.execute("BEGIN IMMEDIATE")
            pending = self.db.execute("SELECT COUNT(*) FROM opportunity_imports WHERE status IN ('queued','reading','analysing','publishing')").fetchone()[0]
            if pending >= 50:
                raise ImportInputError("The queue is full. Wait for some imports to finish before adding more.")
            now = time.time()
            self.db.execute("""INSERT INTO opportunity_imports
                (id,owner_id,request_id,input_hash,source_url,caption,source_complete,images_json,status,created_at,updated_at,reference_url)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (job_id, owner_id, request_id, fingerprint, url, caption, int(complete), json.dumps(paths), "queued", now, now, reference_url))
            self.db.commit()
        except Exception:
            self.db.rollback()
            if directory.is_dir():
                for path in directory.iterdir():
                    path.unlink()
                directory.rmdir()
            # A simultaneous identical upload may have won the unique constraint.
            existing = self.db.execute("SELECT * FROM opportunity_imports WHERE owner_id=? AND request_id=?", (owner_id, request_id)).fetchone()
            if existing and existing["input_hash"] == fingerprint:
                return self.public(dict(existing))
            raise
        return self.public(self.get(job_id))

    def retry(self, job_id: str, owner_id: str, caption: str, complete: bool, reference_url: str | None = None):
        job = self.get(job_id, owner_id)
        if not job:
            raise KeyError(job_id)
        if job["status"] == "published" and job["matching_status"] == "paused":
            self.update(job_id, matching_status="pending", message="Published. Retrying remaining matches.")
        elif job["status"] in RETRYABLE:
            if len(caption) > 20_000:
                raise ImportInputError("The caption must be under 20,000 characters.")
            # Explicit retries may incur one fresh analysis; interrupted jobs never retry themselves.
            reference_url = job["reference_url"] if reference_url is None else canonical_source(reference_url) if reference_url.strip() else ""
            changed = caption.strip() != job["caption"] or complete != bool(job["source_complete"]) or reference_url != job["reference_url"]
            replacement_hash = job.get("replacement_hash", "")
            if job["opportunity_id"]:
                current = self.storage.get_opportunity_for_admin(job["opportunity_id"])
                original = json.loads(job["opportunity_json"] or "{}")
                expected = replacement_hash or original.get("stageviva_review", {}).get("catalogue_snapshot_hash") or _record_hash(original)
                if (not current or current["visible"]
                        or current.get("hidden_reason") not in {"needs_verification", "unverified_application", "needs_information", "source_conflict"}
                        or current["opportunity"].get("stageviva_review", {}).get("import_id") != job_id
                        or _record_hash(current["opportunity"]) != expected):
                    raise ImportInputError("This saved opportunity was edited, hidden manually or published. Open the saved opportunity to preserve those changes.")
                replacement_hash = _record_hash(current["opportunity"])
            self.update(job_id, status="queued", message="", caption=caption.strip(), source_complete=int(complete),
                        reference_url=reference_url,
                        replacement_hash=replacement_hash,
                        source_text=None if changed else job["source_text"], opportunity_json=None,
                        failure_code="", failure_stage="", failure_type="")
            self.db.execute("UPDATE opportunity_imports SET notification_read_at=NULL WHERE id=?", (job_id,))
            self.db.commit()
        else:
            raise ImportInputError("This import is already processing or has finished. Open its review instead.")
        return self.public(self.get(job_id))

    def recover(self):
        # Uncertain paid calls are never automatically repeated after a crash.
        cutoff = time.time() - 300
        self.db.execute("""UPDATE opportunity_imports SET status='queued', updated_at=?
            WHERE status IN ('reading','analysing') AND opportunity_json IS NOT NULL AND updated_at<?""", (time.time(), cutoff))
        self.db.execute("""UPDATE opportunity_imports SET status='interrupted', message='Processing was interrupted. Retry to continue; a fresh analysis may be needed.', updated_at=?
            WHERE status IN ('reading','analysing') AND updated_at<?""", (time.time(), cutoff))
        self.db.execute("""UPDATE opportunity_imports SET matching_status='paused', message='Published. Matching was interrupted; retry to finish.', updated_at=?
            WHERE matching_status='running' AND updated_at<?""", (time.time(), cutoff))
        self.db.commit()

    def claim(self):
        self.db.execute("BEGIN IMMEDIATE")
        row = self.db.execute("""SELECT job.* FROM opportunity_imports job
            WHERE (job.status='queued' OR (job.status='publishing' AND job.updated_at<?))
            AND NOT EXISTS (SELECT 1 FROM opportunity_imports other
                WHERE other.id<>job.id AND other.source_url=job.source_url
                AND other.status IN ('reading','analysing','publishing'))
            ORDER BY job.created_at LIMIT 1""", (time.time() - 300,)).fetchone()
        if row:
            # Publishing is resumable because its extracted JSON is already durable.
            self.db.execute("UPDATE opportunity_imports SET status='reading',updated_at=? WHERE id=?", (time.time(), row["id"]))
        self.db.commit()
        return dict(row) if row else None

    def read_source(self, job):
        if job["source_text"] and job.get("source_reader_version") == SOURCE_READER_VERSION:
            return job["source_text"], json.loads(job["research_json"]), job["source_kind"]
        parts = []
        if job["caption"]:
            parts.append("Owner-supplied full caption:\n" + job["caption"])
        filenames = json.loads(job["images_json"])
        for index, filename in enumerate(filenames):
            path = Path(filename).resolve()
            if path.parent != (self.root / job["id"]).resolve():
                raise ImportInputError("Screenshot could not be read. Upload it again.")
            try:
                text = _extract_image_text(path, 12_000, timeout=20).strip()
            except Exception as error:
                raise ImportInputError("A screenshot could not be read. Upload a clearer image or remove it and provide the full caption in a new import.") from error
            if len(re.sub(r"\W", "", text)) < 25:
                raise ImportInputError("A screenshot contains too little readable text. Provide a clearer image.")
            parts.append(f"Screenshot {index + 1} of {len(filenames)} (same announcement, including caption):\n" + text)
        links = [job["reference_url"]] if job["reference_url"] else []
        kind = "caption"
        if screenshot_source(job["source_url"]):
            if not (job["caption"] or job["source_complete"]):
                raise ImportInputError("Confirm the screenshots include the complete announcement and caption, or add the missing text.")
        elif not instagram_url(job["source_url"]):
            try:
                html, destination = fetch_public_html(job["source_url"], DEFAULT_HEADERS, timeout=12)
                text = extract_useful_text(html, 20_000)
                if len(text.strip()) < 80:
                    raise PublicSourceError("The page contains too little readable text.")
                parts.append(f"Fetched listing ({destination}):\n{text}")
                link = extract_official_application_url(html, destination)
                links += ([link] if link and link != destination else []) + html_research_links(html, destination)
                # Only the actual fetched source is eligible as an application destination.
                kind = "listing" if destination == job["source_url"] else "caption"
            except PublicSourceError as error:
                if not (job["caption"] or job["source_complete"]):
                    raise ImportInputError("The source page could not be read. Paste its full announcement or attach complete screenshots.") from error
        elif not (job["caption"] or job["source_complete"]):
            raise ImportInputError("Paste the full Instagram caption, or confirm the screenshots contain the complete announcement.")
        combined = "\n\n".join(parts)
        if len(combined) > 45_000:
            raise ImportInputError("This group contains too much text to analyse completely. Remove duplicate/overlapping screenshots or split separate announcements into different groups.")
        if len(re.sub(r"\W", "", combined)) < 60:
            raise ImportInputError("Add the full announcement text or readable screenshots before analysing.")
        links = [link for link in dict.fromkeys([*links, *research_links(combined)]) if link != job["source_url"]][:2]
        self.update(job["id"], source_text=combined, source_kind=kind, research_json=json.dumps(links), source_reader_version=SOURCE_READER_VERSION)
        return combined, links, kind

    def process(self, job, authorised):
        job_id, url = job["id"], job["source_url"]
        try:
            if not authorised(job["owner_id"]):
                raise ImportInputError("Administrator access is required to continue this import.")
            existing = self.storage.opportunity_by_listing_url(url)
            if existing and existing["opportunity"].get("stageviva_review", {}).get("import_id") != job_id:
                self.update(job_id, status="duplicate", opportunity_id=existing["id"], message="This source is already in the catalogue. Its existing record was preserved.")
                return
            if job["opportunity_json"]:
                opportunity = json.loads(job["opportunity_json"])
            else:
                text, links, kind = self.read_source(job)
                self.update(job_id, status="analysing", message="Reading the announcement and linked application pages.")
                opportunity = analyse_opportunity(url, listing_text=text, research_urls=links, listing_text_kind=kind)
                opportunity.setdefault("stageviva_review", {})["required_content_version"] = "complete-opportunity-v1"
                opportunity = prepare_publication(opportunity, job["reference_url"])
                if screenshot_source(url):
                    for field in opportunity.get("source", {}).values():
                        if isinstance(field, dict) and str(field.get("value", "")).startswith(UPLOAD_PREFIX):
                            field.update(value="unknown", evidence=[], confidence="unknown")
                opportunity.setdefault("stageviva_review", {})["import_id"] = job_id
                opportunity["stageviva_review"]["review_policy"] = PUBLICATION_POLICY
                self.update(job_id, status="publishing", opportunity_json=json.dumps(opportunity, ensure_ascii=False))
            if not authorised(job["owner_id"]):
                raise ImportInputError("Administrator access is required to publish this import.")
            review = opportunity.get("stageviva_review", {})
            reason = publication_reason(opportunity)
            title = str(value_at(opportunity, "identity", "title") or "Imported announcement")
            item = DiscoveredOpportunity(title, url, "StageViva import", job["reference_url"] if screenshot_source(url) else url, "manual")
            # Serialize duplicate check and insert across workers. upsert commits this transaction.
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.storage.opportunity_by_listing_url(url)
            if existing:
                ours = existing["opportunity"].get("stageviva_review", {}).get("import_id") == job_id
                replace = (ours and job.get("replacement_hash") and not existing["visible"]
                           and existing.get("hidden_reason") in {"needs_verification", "unverified_application", "needs_information", "source_conflict"}
                           and _record_hash(existing["opportunity"]) == job["replacement_hash"])
                if not replace:
                    self.db.rollback()
                    self.update(job_id, status="published" if ours and existing["visible"] else "review" if ours else "duplicate",
                                opportunity_id=existing["id"], matching_status="pending" if ours and existing["visible"] else "",
                                message="Published. Preparing matches." if ours and existing["visible"] else "The saved record was preserved. Open it to review any manual changes.")
                    return
            duplicate = self.storage.duplicate_of({"id": existing["id"] if existing else job_id, "listing_url": url, "title": title, "source_name": "StageViva import", "opportunity": opportunity}, exclude_id=existing["id"] if existing else None)
            if duplicate:
                reason = "duplicate"
                review["duplicate_of"] = duplicate["id"]
            opportunity_id = self.storage.upsert_opportunity(item, opportunity, hidden_reason="needs_information" if reason and reason.startswith("missing_") else reason)
            self.update(job_id, status="duplicate" if duplicate else "review" if reason else "published", opportunity_id=opportunity_id,
                        replacement_hash="",
                        matching_status="" if reason else "pending",
                        message=f"Saved for review: {reason.replace('_', ' ')}." if reason else "Published. Preparing matches.")
        except AIRequestsDisabledError:
            self.db.rollback()
            self.update(job_id, status="paused", message="AI usage is paused or the daily allowance is reached. Retry when it is available.")
        except ImportInputError as error:
            self.db.rollback()
            self.update(job_id, status="needs_information", message=str(error))
        except Exception as error:
            self.db.rollback()
            # Provider errors can include signed URLs or private response data.
            code, message = import_failure(error)
            stage = self.get(job_id)["status"]
            self.update(job_id, status="failed", message=message, failure_code=code, failure_stage=stage, failure_type=type(error).__name__[:80])

    def reconcile_reviews(self, authorised):
        """Recheck saved AI results once per policy version, without AI or HTTP.

        Only untouched automatic review holds belonging to a current admin are
        eligible. Manual hides, edits, expired rows and duplicates stay intact.
        """
        jobs = self.db.execute("""SELECT * FROM opportunity_imports WHERE status='review'
            AND opportunity_json IS NOT NULL
            AND COALESCE(json_extract(opportunity_json, '$.stageviva_review.review_policy'), '')<>?
            ORDER BY created_at LIMIT 10""", (PUBLICATION_POLICY,)).fetchall()
        released = 0
        for row in jobs:
            job = dict(row)
            original = json.loads(job["opportunity_json"])
            expected_hash = original.get("stageviva_review", {}).get("catalogue_snapshot_hash") or _record_hash(original)
            prepared = prepare_publication(original, job["reference_url"])
            prepared.setdefault("stageviva_review", {})["review_policy"] = PUBLICATION_POLICY
            prepared["stageviva_review"]["catalogue_snapshot_hash"] = expected_hash
            current = self.storage.get_opportunity_for_admin(job["opportunity_id"]) if job["opportunity_id"] else None
            if not current or not authorised(job["owner_id"]):
                self.update(job["id"], opportunity_json=json.dumps(prepared, ensure_ascii=False))
                continue
            ours = current["opportunity"].get("stageviva_review", {}).get("import_id") == job["id"]
            if not ours:
                self.update(job["id"], opportunity_json=json.dumps(prepared, ensure_ascii=False))
                continue
            if current["visible"]:
                self.update(job["id"], status="published", matching_status="pending", opportunity_json=json.dumps(current["opportunity"], ensure_ascii=False), message="Published. Preparing matches.")
                continue
            if current.get("hidden_reason") not in {"needs_verification", "unverified_application", "needs_information"} or _record_hash(current["opportunity"]) != expected_hash:
                self.update(job["id"], opportunity_json=json.dumps(prepared, ensure_ascii=False))
                continue
            reason = publication_reason(prepared)
            if reason:
                self.update(job["id"], opportunity_json=json.dumps(prepared, ensure_ascii=False), message=f"More information needed: {reason.replace('_', ' ')}.")
                continue
            self.db.execute("BEGIN IMMEDIATE")
            latest = self.storage.get_opportunity_for_admin(current["id"])
            if not latest or latest["visible"] or not authorised(job["owner_id"]) or latest.get("hidden_reason") != current.get("hidden_reason") or latest["opportunity"] != current["opportunity"]:
                self.db.rollback()
                continue
            duplicate = self.storage.duplicate_of({**latest, "opportunity": prepared}, exclude_id=latest["id"])
            if duplicate:
                self.storage.hide_opportunity(latest["id"], reason="duplicate")
                self.update(job["id"], status="duplicate", opportunity_json=json.dumps(prepared, ensure_ascii=False), message="This opportunity is already live in another listing.")
                continue
            item = DiscoveredOpportunity(latest["title"], latest["listing_url"], latest["source_name"], latest["source_url"], latest["category"])
            self.storage.upsert_opportunity(item, prepared)
            self.update(job["id"], status="published", matching_status="pending", opportunity_json=json.dumps(prepared, ensure_ascii=False), message="Published automatically after rechecking the saved source. Preparing matches.")
            released += 1
        return released

    def match_one(self, authorised, matcher):
        self.db.execute("BEGIN IMMEDIATE")
        row = self.db.execute("SELECT * FROM opportunity_imports WHERE status='published' AND matching_status='pending' ORDER BY updated_at LIMIT 1").fetchone()
        if row:
            self.db.execute("UPDATE opportunity_imports SET matching_status='running',updated_at=? WHERE id=?", (time.time(), row["id"]))
        self.db.commit()
        if not row:
            return False
        job = dict(row)
        try:
            if not authorised(job["owner_id"]):
                raise ImportInputError("Administrator access is required.")
            # Read the latest record so an owner's subsequent edits/hide always win.
            current = self.storage.opportunity_by_listing_url(job["source_url"])
            if not current or not current["visible"]:
                self.update(job["id"], matching_status="complete", message="Matching stopped because the opportunity is hidden.")
                return True
            result = matcher(current["id"], current["opportunity"], self.storage)
            remaining = result.get("remaining", False)
            self.update(job["id"], matching_status="pending" if remaining else "complete",
                        message="Published. Preparing matches." if remaining else "Published and matches prepared.")
        except Exception:
            self.update(job["id"], matching_status="paused", message="Published. Matching is paused; retry to finish remaining matches.")
        return True
