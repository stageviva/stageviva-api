from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage import StageVivaStorage


class AccountDeletionTest(unittest.TestCase):
    def test_deleting_an_account_removes_user_artist_matches_and_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = StageVivaStorage(Path(directory) / "stageviva.db")
            try:
                user = storage.create_user("performer@example.com", "hash", "Performer")
                artist_id = storage.upsert_artist_for_user(user["id"], {"identity": {"name": "Performer"}})
                storage.connection.execute(
                    "INSERT INTO cv_analysis_jobs (user_id, status, started_at) VALUES (?, 'complete', 'now')",
                    (user["id"],),
                )
                storage.connection.execute(
                    "INSERT INTO cv_upload_events (id, user_id, created_at) VALUES ('upload', ?, 'now')",
                    (user["id"],),
                )
                storage.connection.execute(
                    "INSERT INTO weekly_match_releases (user_id, week_start, opportunity_ids_json, created_at) VALUES (?, '2026-09-21', '[]', 'now')",
                    (user["id"],),
                )
                storage.connection.execute(
                    "INSERT INTO match_results (artist_id, opportunity_id, match_json, updated_at) VALUES (?, 'opportunity', '{}', 'now')",
                    (artist_id,),
                )
                storage.connection.commit()

                self.assertIsNotNone(storage.delete_user_account(user["id"]))
                self.assertIsNone(storage.get_user(user["id"]))
                self.assertIsNone(storage.connection.execute("SELECT 1 FROM artists WHERE id = ?", (artist_id,)).fetchone())
                self.assertIsNone(storage.connection.execute("SELECT 1 FROM match_results WHERE artist_id = ?", (artist_id,)).fetchone())
                self.assertIsNone(storage.connection.execute("SELECT 1 FROM cv_analysis_jobs WHERE user_id = ?", (user["id"],)).fetchone())
                self.assertIsNone(storage.connection.execute("SELECT 1 FROM weekly_match_releases WHERE user_id = ?", (user["id"],)).fetchone())
            finally:
                storage.close()
