"""Opportunity Intelligence checks and an optional live smoke test."""

import json
import unittest

from opportunity_intelligence import DiscoveryContext, analyse_opportunity, extract_official_application_url

LEIPZIG_URL = "https://balletplaces.com/auditions/leipzig-ballet-audition-13-september-2026/"


class OfficialApplicationLinkTest(unittest.TestCase):
    def test_prefers_external_application_link_from_directory_listing(self) -> None:
        html = '<a href="https://example-ballet.org/careers/audition">Apply now</a>'
        self.assertEqual(
            extract_official_application_url(html, LEIPZIG_URL),
            "https://example-ballet.org/careers/audition",
        )

    def test_keeps_direct_company_listing_url(self) -> None:
        company_url = "https://example-ballet.org/auditions/2027"
        self.assertEqual(extract_official_application_url("", company_url), company_url)


def main() -> None:
    result = analyse_opportunity(
        LEIPZIG_URL,
        DiscoveryContext(
            source_name="BalletPlaces",
            source_url="https://balletplaces.com/ballet-auditions/accepting-applications/",
            category="ballet_dance",
            title="Leipzig Ballet Audition",
        ),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
