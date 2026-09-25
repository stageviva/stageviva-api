from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage import StageVivaStorage


def field(value: str) -> dict[str, str]:
    return {"value": value}


def opportunity(title: str, *, description: str = "", official_url: str = "") -> dict:
    return {
        "identity": {"title": field(title), "organisation": field("Unknown"), "description": field(description)},
        "location": {"city": field("Liverpool"), "country": field("United Kingdom")},
        "dates": {"application_deadline": field("16 October 2026")},
        "application": {"application_url": field(official_url)},
        "source": {"official_url": field(official_url)},
    }


class OpportunityDeduplicationTest(unittest.TestCase):
    def test_same_official_url_hides_the_less_complete_repost(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = StageVivaStorage(Path(directory) / "stageviva.db")
            try:
                first = {
                    "id": "first", "listing_url": "https://board.example/first", "title": "Dancers Required",
                    "source_name": "Board", "visible": True,
                    "opportunity": opportunity("Dancers Required", official_url="https://employer.example/apply"),
                }
                second = {
                    "id": "second", "listing_url": "https://board.example/second", "title": "Production Dancers Needed",
                    "source_name": "Board", "visible": True,
                    "opportunity": opportunity("Production Dancers Needed", description="Full cruise contract", official_url="https://employer.example/apply"),
                }
                storage.connection.executemany(
                    "INSERT INTO opportunities (id, listing_url, title, source_name, source_url, category, opportunity_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (first["id"], first["listing_url"], first["title"], "Board", "https://board.example", "dance", __import__("json").dumps(first["opportunity"]), "now", "now"),
                        (second["id"], second["listing_url"], second["title"], "Board", "https://board.example", "dance", __import__("json").dumps(second["opportunity"]), "now", "now"),
                    ],
                )
                storage.connection.commit()
                self.assertEqual(storage.hide_duplicate_opportunities(), 1)
                visible = [item["id"] for item in storage.list_all_opportunities() if item["visible"]]
                self.assertEqual(visible, ["second"])
            finally:
                storage.close()

    def test_same_source_city_deadline_and_near_identical_titles_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = StageVivaStorage(Path(directory) / "stageviva.db")
            try:
                entries = [
                    ("first", "https://board.example/first", "Dancers Required For Cruise Contract Liverpool UK Auditions", opportunity("Dancers Required")),
                    ("second", "https://board.example/second", "Production Dancers Needed For Cruise Contract Liverpool UK Auditions", opportunity("Production Dancers Needed", description="Full cruise contract")),
                ]
                storage.connection.executemany(
                    "INSERT INTO opportunities (id, listing_url, title, source_name, source_url, category, opportunity_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (entry_id, url, title, "Board", "https://board.example", "dance", __import__("json").dumps(data), "now", "now")
                        for entry_id, url, title, data in entries
                    ],
                )
                storage.connection.commit()
                self.assertEqual(storage.hide_duplicate_opportunities(), 1)
            finally:
                storage.close()
