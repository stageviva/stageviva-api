from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from storage import StageVivaStorage, _same_live_opportunity


def field(value: str) -> dict[str, str]:
    return {"value": value}


def opportunity(title: str, *, description: str = "", official_url: str = "") -> dict:
    return {
        "identity": {"title": field(title), "organisation": field("Unknown"), "description": field(description)},
        "location": {"city": field("Liverpool"), "country": field("United Kingdom")},
        "dates": {"application_deadline": field((date.today() + timedelta(days=30)).strftime("%d %B %Y"))},
        "application": {"application_url": field(official_url)},
        "source": {"official_url": field(official_url)},
    }


class OpportunityDeduplicationTest(unittest.TestCase):
    def test_shared_login_advert_and_social_profile_do_not_merge_distinct_jobs(self) -> None:
        for url in (
            "https://www.entertainersworldwide.com/user/gigs.php",
            "https://artcasse.com/?aff=advert",
            "https://www.facebook.com/allcastingcom",
        ):
            with self.subTest(url=url):
                first = {"listing_url": "https://board.example/one", "source_name": "Board", "title": "Principal bass player",
                         "opportunity": opportunity("Principal bass player", official_url=url)}
                second = {"listing_url": "https://board.example/two", "source_name": "Board", "title": "Commercial model campaign",
                          "opportunity": opportunity("Commercial model campaign", official_url=url)}
                self.assertFalse(_same_live_opportunity(first, second))

    def test_generic_link_repair_preserves_manual_hides_and_expired_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = StageVivaStorage(Path(directory) / "stageviva.db")
            try:
                expired = opportunity("Expired cast", official_url="https://artcasse.com/?aff=advert")
                expired["dates"]["application_deadline"] = field("1 January 2020")
                records = [
                    ("recover", "duplicate", opportunity("Principal bass player", official_url="https://artcasse.com/?aff=advert")),
                    ("manual", "manual", opportunity("Commercial campaign", official_url="https://artcasse.com/?aff=advert")),
                    ("expired", "duplicate", expired),
                ]
                storage.connection.executemany("""
                    INSERT INTO opportunities (id, listing_url, title, source_name, source_url, category,
                        opportunity_json, visible, hidden_reason, created_at, updated_at)
                    VALUES (?, ?, ?, 'Board', 'https://board.example', 'dance', ?, 0, ?, 'now', 'now')
                """, [(identifier, f"https://board.example/{identifier}", data["identity"]["title"]["value"],
                       __import__("json").dumps(data), reason) for identifier, reason, data in records])
                storage.connection.commit()
                self.assertEqual(storage.restore_false_generic_url_duplicates(), 1)
                self.assertEqual(storage.restore_false_generic_url_duplicates(), 0)
                visibility = {row["id"]: row["visible"] for row in storage.list_all_opportunities()}
                self.assertEqual(visibility, {"recover": True, "manual": False, "expired": False})
            finally:
                storage.close()

    def test_restores_current_legacy_hidden_source_listing_but_preserves_manual_hide(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = StageVivaStorage(Path(directory) / "stageviva.db")
            try:
                rows = [
                    (
                        "legacy", "https://board.example/current", "Current listing", "Trusted Board",
                        "dance", opportunity("Current listing"), 0, None,
                    ),
                    (
                        "manual", "https://board.example/manual-hide", "Founder hidden", "Trusted Board",
                        "dance", opportunity("Founder hidden"), 0, "manual",
                    ),
                ]
                storage.connection.executemany("""
                    INSERT INTO opportunities (
                        id, listing_url, title, source_name, source_url, category,
                        opportunity_json, visible, hidden_reason, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'https://board.example', ?, ?, ?, ?, 'now', 'now')
                """, [
                    (entry_id, url, title, source_name, category, __import__("json").dumps(data), visible, reason)
                    for entry_id, url, title, source_name, category, data, visible, reason in rows
                ])
                storage.connection.commit()

                self.assertEqual(storage.restore_current_legacy_hidden_opportunities(), 1)
                visibility = {
                    item["id"]: item["visible"] for item in storage.list_all_opportunities()
                }
                self.assertTrue(visibility["legacy"])
                self.assertFalse(visibility["manual"])
            finally:
                storage.close()

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
