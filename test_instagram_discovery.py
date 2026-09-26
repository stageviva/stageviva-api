from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

import requests

from instagram_discovery import discover_instagram_source, instagram_is_configured
from source_registry import Source


SOURCE = Source(
    "Instagram @example", "https://www.instagram.com/example/", "ballet_dance",
    automation_ready=True, source_type="instagram", instagram_handle="example",
)


class InstagramDiscoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(os.environ, {
            "STAGEVIVA_INSTAGRAM_ACCESS_TOKEN": "test-token",
            "STAGEVIVA_INSTAGRAM_BUSINESS_ACCOUNT_ID": "123",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_discovers_relevant_caption_with_permalink(self) -> None:
        response = Mock()
        response.json.return_value = {"business_discovery": {"media": {"data": [{
            "caption": "AUDITION: professional dancers wanted. Apply at https://example.org/apply",
            "permalink": "https://www.instagram.com/p/example/",
            "timestamp": "2026-09-26T10:00:00+0000",
        }, {
            "caption": "Behind the scenes from our rehearsal.",
            "permalink": "https://www.instagram.com/p/not-a-listing/",
        }]}}}
        with patch("instagram_discovery.requests.get", return_value=response) as get:
            opportunities = discover_instagram_source(SOURCE)
        self.assertEqual(len(opportunities), 1)
        self.assertEqual(opportunities[0].listing_url, "https://www.instagram.com/p/example/")
        self.assertIn("https://example.org/apply", opportunities[0].description or "")
        self.assertIn("business_discovery.username(example)", get.call_args.kwargs["params"]["fields"])

    def test_missing_configuration_skips_safely(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(instagram_is_configured())
            self.assertEqual(discover_instagram_source(SOURCE), [])

    def test_api_failure_never_includes_access_token(self) -> None:
        response = Mock(status_code=400)
        response.json.return_value = {"error": {"code": 190, "message": "Invalid OAuth token"}}
        response.raise_for_status.side_effect = requests.HTTPError(
            "400 Client Error: Bad Request for url: https://example.test/?access_token=test-token"
        )
        with patch("instagram_discovery.requests.get", return_value=response):
            with self.assertRaises(RuntimeError) as raised:
                discover_instagram_source(SOURCE)
        self.assertNotIn("test-token", str(raised.exception))
        self.assertNotIn("access_token", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
