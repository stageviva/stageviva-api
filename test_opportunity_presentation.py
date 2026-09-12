from __future__ import annotations

import unittest

from opportunity_presentation import opportunity_card, opportunity_detail


class OpportunityPresentationTest(unittest.TestCase):
    def test_unknown_company_keeps_the_real_listing_title(self) -> None:
        item = {
            "opportunity": {
                "identity": {
                    "organisation": {"value": "unknown"},
                    "title": {"value": "Dancers Who Sing Required For Broadway Musical"},
                    "description": {"value": "A Broadway musical is casting dancers who sing."},
                    "opportunity_type": {"value": "Audition"},
                },
                "dates": {"application_deadline": {"value": "unknown"}},
                "location": {"city": {"value": "New York"}, "country": {"value": "USA"}},
                "contract_and_compensation": {"contract_type": {"value": "unknown"}},
            },
            "match": {"overall": {"match_score": 80, "match_level": "good"}},
        }
        card = opportunity_card(item)
        self.assertEqual(card["title"], "Dancers Who Sing Required For Broadway Musical")

    def test_unknown_application_url_falls_back_to_the_listing_page(self) -> None:
        item = {
            "opportunity_id": "opportunity_1",
            "listing_url": "https://example.org/audition",
            "match": {"overall": {}},
            "opportunity": {
                "identity": {"title": {"value": "Audition"}},
                "application": {"application_url": {"value": "unknown"}},
                "source": {"official_url": {"value": "unknown"}},
            },
        }
        self.assertEqual(opportunity_detail(item)["application"]["url"], "https://example.org/audition")

    def test_directory_listing_is_not_presented_as_an_official_apply_link(self) -> None:
        item = {
            "opportunity_id": "opportunity_1",
            "listing_url": "https://balletplaces.com/auditions/example",
            "match": {"overall": {}},
            "opportunity": {
                "identity": {"title": {"value": "Audition"}},
                "application": {"application_url": {"value": "unknown"}},
                "source": {"official_url": {"value": "unknown"}},
            },
        }
        self.assertEqual(opportunity_detail(item)["application"]["url"], "")

    def test_representation_listings_are_an_agency_category(self) -> None:
        item = {
            "opportunity": {"identity": {
                "organisation": {"value": "Example Talent Agency"},
                "title": {"value": "Seeking dance representation"},
                "description": {"value": "Agency representation for professional dancers."},
                "opportunity_type": {"value": "Representation"},
            }},
            "match": {"overall": {}},
        }
        self.assertIn("agency", opportunity_card(item)["categories"])

    def test_categories_use_structured_requirements_not_only_the_title(self) -> None:
        item = {
            "opportunity": {
                "identity": {"organisation": {"value": "Example Dance"}, "title": {"value": "Company audition"}},
                "requirements": {
                    "styles": {"value": ["Contemporary"]},
                    "discipline_requirements": {"jazz": {"required_level": "high"}},
                },
            },
            "match": {"overall": {}},
        }
        categories = opportunity_card(item)["categories"]
        self.assertIn("contemporary", categories)
        self.assertIn("jazz", categories)


if __name__ == "__main__":
    unittest.main()
