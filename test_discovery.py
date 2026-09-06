from __future__ import annotations

import unittest
from unittest.mock import patch

from opportunity_discovery import discover_allcasting, discover_ballee, discover_dance_europe, discover_entertainers_worldwide


PAGE = """
<div class='rich-text'><h4><b>CASTING CALL - DANCER / ACTOR</b></h4>
<p>Seeking a dancer-actor for a paid English-language web series. Send CV to
<a href='mailto:casting@example.com'>casting@example.com</a>.</p></div>
<div class='rich-text'><h4>THE EXAMPLE BALLET - DANCERS</h4>
<p>Example Ballet seeks professional dancers for its 2027 season with strong ballet training.
Apply at <a href='https://example.org/jobs'>the official page</a>.</p></div>
"""


class DanceEuropeDiscoveryTest(unittest.TestCase):
    @patch("opportunity_discovery.fetch_page", return_value=PAGE)
    def test_extracts_email_only_and_official_link_notices(self, _fetch) -> None:
        opportunities = discover_dance_europe()
        self.assertEqual(len(opportunities), 2)
        self.assertTrue(opportunities[0].listing_url.startswith("https://danceeurope.net/auditions/#notice-"))
        self.assertEqual(opportunities[1].listing_url, "https://example.org/jobs")
        self.assertIn("paid", opportunities[0].description or "")


class BalleeDiscoveryTest(unittest.TestCase):
    @patch("opportunity_discovery.fetch_page", return_value="""
        <a href='/auditions/example-company-audition'>Example Company Audition</a>
        <a href='/auditions/companies/a'>A</a>
        <a href='/auditions'>All auditions</a>
    """)
    def test_extracts_only_individual_audition_pages(self, _fetch) -> None:
        opportunities = discover_ballee()
        self.assertEqual(len(opportunities), 1)
        self.assertEqual(opportunities[0].listing_url, "https://ballee.co/auditions/example-company-audition")


class EntertainersWorldwideDiscoveryTest(unittest.TestCase):
    @patch("opportunity_discovery.fetch_page", return_value="""
        <a href='/dancer-jobs/dancers-needed-cruise-123'>Dancers Needed</a>
        <a href='/dancer-jobs/dancers-needed-cruise-123/apply'>Apply</a>
        <a href='/photographer-jobs/photo-job-987'>Photography job</a>
    """)
    def test_keeps_public_performer_listing_and_skips_apply_links(self, _fetch) -> None:
        opportunities = discover_entertainers_worldwide()
        self.assertEqual(len(opportunities), 1)
        self.assertEqual(opportunities[0].title, "Dancers Needed")


class AllCastingDiscoveryTest(unittest.TestCase):
    @patch("opportunity_discovery.fetch_page", return_value="""
        <a href='/castingcall/318716'>Casting Talent for a Campaign</a>
        <a href='/blog/casting-call-advice'>Casting-call advice</a>
    """)
    def test_keeps_only_individual_casting_calls(self, _fetch) -> None:
        opportunities = discover_allcasting()
        self.assertEqual(len(opportunities), 1)
        self.assertEqual(opportunities[0].listing_url, "https://allcasting.com/castingcall/318716")


if __name__ == "__main__":
    unittest.main()
