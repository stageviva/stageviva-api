from __future__ import annotations

import unittest

from opportunity_presentation import opportunity_card


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


if __name__ == "__main__":
    unittest.main()
