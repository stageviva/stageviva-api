"""Offline test of the complete central StageViva pipeline."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opportunity_discovery import DiscoveredOpportunity
from source_registry import Source
from stageviva_pipeline import run_pipeline
from storage import StageVivaStorage

ARTIST = {"identity": {"name": "Pipeline Test Dancer"}, "disciplines": []}
OPPORTUNITY = {"identity": {"title": {"value": "Test audition"}}}
MATCH = {"overall": {"match_score": 75, "match_level": "strong", "recommendation": "good_match", "summary": "Test result", "confidence": "high"}}


class PipelineTest(unittest.TestCase):
    def test_pipeline_stores_and_deduplicates_a_match_notification(self) -> None:
        source = Source("Test Source", "https://example.test/auditions", "ballet_dance")
        item = DiscoveredOpportunity("Test audition", "https://example.test/auditions/123", source.name, source.url, source.category)
        with tempfile.TemporaryDirectory() as directory:
            storage = StageVivaStorage(Path(directory) / "stageviva.db")
            try:
                first = run_pipeline(source, [ARTIST], storage,
                    discover=lambda _: [item], analyse=lambda _url, _context: OPPORTUNITY,
                    match=lambda _artist, _opportunity: MATCH)
                self.assertEqual((first.analysed, first.stored_matches, first.queued_notifications), (1, 1, 1))
                self.assertEqual((storage.count("opportunities"), storage.count("match_results"), storage.count("notification_outbox")), (1, 1, 1))
                new_item = DiscoveredOpportunity("Second audition", "https://example.test/auditions/456", source.name, source.url, source.category)
                second = run_pipeline(source, [ARTIST], storage, limit=1,
                    discover=lambda _: [item, new_item], analyse=lambda _url, _context: OPPORTUNITY,
                    match=lambda _artist, _opportunity: MATCH)
                self.assertEqual(second.skipped_existing, 1)
                self.assertEqual(second.analysed, 1)
                self.assertEqual(storage.count("notification_outbox"), 2)
            finally:
                storage.close()

    def test_registered_artist_is_matched_without_a_local_json_file(self) -> None:
        source = Source("Test Source", "https://example.test/auditions", "ballet_dance")
        item = DiscoveredOpportunity("Test audition", "https://example.test/auditions/789", source.name, source.url, source.category)
        with tempfile.TemporaryDirectory() as directory:
            storage = StageVivaStorage(Path(directory) / "stageviva.db")
            try:
                user = storage.create_user("pipeline@example.com", "hash", "Pipeline Dancer")
                storage.upsert_artist_for_user(user["id"], ARTIST)
                result = run_pipeline(source, None, storage, limit=1,
                    discover=lambda _: [item], analyse=lambda _url, _context: OPPORTUNITY,
                    match=lambda _artist, _opportunity: MATCH)
                self.assertEqual(result.stored_matches, 1)
                self.assertEqual(result.queued_notifications, 1)
                self.assertEqual(len(storage.list_notifications_for_user(user["id"])), 1)
            finally:
                storage.close()

    def test_expired_listing_is_rejected_once_and_never_matched(self) -> None:
        source = Source("Test Source", "https://example.test/auditions", "ballet_dance")
        item = DiscoveredOpportunity("Expired audition", "https://example.test/auditions/expired", source.name, source.url, source.category)
        expired = {"dates": {"application_deadline": {"value": "1 January 2020"}, "audition_dates": {"value": []}}}
        with tempfile.TemporaryDirectory() as directory:
            storage = StageVivaStorage(Path(directory) / "stageviva.db")
            try:
                first = run_pipeline(source, [ARTIST], storage, discover=lambda _: [item],
                    analyse=lambda _url, _context: expired,
                    match=lambda *_: self.fail("Expired listing must not be matched"))
                self.assertEqual(first.skipped_expired, 1)
                second = run_pipeline(source, [ARTIST], storage, discover=lambda _: [item],
                    analyse=lambda *_: self.fail("Rejected URL must not be analysed again"),
                    match=lambda *_: self.fail("Rejected URL must not be matched"))
                self.assertEqual(second.skipped_existing, 1)
            finally:
                storage.close()


if __name__ == "__main__":
    unittest.main()
