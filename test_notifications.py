from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from email_notifications import deliver_pending_emails
from opportunity_discovery import DiscoveredOpportunity
from storage import StageVivaStorage


class NotificationTest(unittest.TestCase):
    def test_email_preview_respects_user_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = StageVivaStorage(Path(directory) / "stageviva.db")
            try:
                user = storage.create_user("notify@example.com", "hash", "Nora Notify")
                artist_id = storage.upsert_artist_for_user(user["id"], {"identity": {"name": "Nora Notify"}})
                item = DiscoveredOpportunity("Test audition", "https://example.test/audition", "Test", "https://example.test", "dance")
                opportunity_id = storage.upsert_opportunity(item, {"identity": {"title": {"value": "Test audition"}}})
                match = {"overall": {"match_score": 85, "recommendation": "strong_match", "summary": "A great fit."}}
                self.assertTrue(storage.queue_notification(artist_id, opportunity_id, match))
                preview = deliver_pending_emails(storage, dry_run=True)
                self.assertEqual(preview["pending"], 1)
                self.assertEqual(preview["preview"][0]["to"], "notify@example.com")

                storage.update_notification_preferences(user["id"], email=False, in_app=False)
                self.assertFalse(storage.queue_notification(artist_id, "another-opportunity", match))
            finally:
                storage.close()


if __name__ == "__main__":
    unittest.main()
