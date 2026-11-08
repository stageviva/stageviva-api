from __future__ import annotations

import unittest

from opportunity_presentation import opportunity_card, opportunity_detail
from storage import merge_opportunity_updates


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
        self.assertEqual(card["title"], "Dancers who sing for Broadway musical")

    def test_card_and_detail_share_clean_role_title_with_pay_separate(self) -> None:
        cases = (
            ("COMPANY THEATRE - MALE CORPS DE BALLET DANCER", "Company Theatre — male corps de ballet dancer"),
            ("CRUISE PRODUCTION DANCERS $1,500 - $2,500 PER WEEK", "Cruise production dancers"),
            ("CRUISE PRODUCTION DANCERS 1500", "Cruise production dancers"),
            ("RESORT SHOW DANCERS", "Resort show dancers"),
            ("CONTEMPORARY COMPANY DANCERS", "Contemporary company dancers"),
            ("MUSICAL THEATRE DANCER-SINGERS", "Musical theatre dancer-singers"),
            ("COMMERCIAL MUSIC VIDEO DANCERS", "Commercial music video dancers"),
            ("CASTING CALL - DANCER / ACTOR", "Dancer / actor"),
            ("COMPANY THEATRE / SOLOIST DANCERS - 2026 -2027 SEASON", "Company Theatre / soloist dancers"),
            ("THE NATIONAL BALLET 2 AUDITION", "The national ballet 2 audition"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                opportunity = {
                    "identity": {"title": {"value": source}, "organisation": {"value": "Company Theatre"}},
                    "contract_and_compensation": {"compensation": {"value": "$1,500–$2,500 per week"}},
                }
                item = {"opportunity_id": "one", "opportunity": opportunity, "match": {}}
                detail = opportunity_detail(item)
                self.assertEqual(opportunity_card(item)["position"], expected)
                self.assertEqual(detail["role"], expected)
                self.assertEqual(detail["company"], "Company Theatre")
                self.assertEqual(detail["compensation"], "$1,500–$2,500 per week")
                self.assertEqual(opportunity["identity"]["title"]["value"], source)

    def test_employer_and_production_names_keep_job_context(self) -> None:
        item = {
            "opportunity_id": "one", "match": {},
            "opportunity": {"identity": {
                "title": {"value": 'CRUISE COMPANY - DANCERS FOR "Mamma Mia!"'},
                "organisation": {"value": "Cruise Company"},
            }},
        }
        self.assertEqual(opportunity_detail(item)["role"], 'Cruise Company — dancers for "Mamma Mia!"')
        item["opportunity"]["identity"]["title"]["value"] = "Cruise dancers for Mamma Mia!"
        item["opportunity"]["stageviva_review"] = {"editorial_style": "sentence_case_with_context_v1"}
        self.assertEqual(opportunity_detail(item)["role"], "Cruise dancers for Mamma Mia!")

    def test_requirements_remove_source_bullets_and_keep_alternatives(self) -> None:
        opportunity = {
            "requirements": {
                "physical": {"height_requirement": {"value": "MINIMUM HEIGHT: 180 CM"},
                             "age_requirement": {"value": "18–25"}},
                "experience": {"required_level": {"value": "- TWO YEARS IN A COMPANY OR A BALLET DIPLOMA"}},
                "styles": {"value": ["• classical ballet", "Classical ballet.", "contemporary dance"]},
                "techniques": {"value": ["* partnering\n- pointe preferred"]},
                "work_rights_or_visa": {"value": "EU work authorisation required\nunless sponsorship is confirmed"},
            },
        }
        detail = opportunity_detail({"opportunity_id": "one", "opportunity": opportunity, "match": {}})
        self.assertEqual(detail["requirements"]["summary"], [
            "Minimum height of 180 cm",
            "Age 18–25", "Two years in a company or a ballet diploma", "Classical ballet",
            "Contemporary dance", "Partnering", "Pointe preferred",
            "EU work authorisation required unless sponsorship is confirmed",
        ])

    def test_editor_experience_correction_replaces_generated_copy(self) -> None:
        opportunity = {"requirements": {"experience": {
            "required_level": {"value": "Two years in a ballet company"},
            "criteria": [{"value": "At least two years in a ballet company"}],
        }}}
        result = merge_opportunity_updates(opportunity, {"experience_required_level": "One year in a ballet company"})
        detail = opportunity_detail({"opportunity_id": "one", "opportunity": result, "match": {}})
        self.assertEqual(detail["requirements"]["summary"], ["One year in a ballet company"])

    def test_unknown_application_url_keeps_source_separate(self) -> None:
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
        detail = opportunity_detail(item)
        self.assertEqual(detail["application"]["url"], "https://example.org/audition")
        self.assertEqual(detail["application"]["label"], "View source listing")
        self.assertEqual(detail["listing_url"], "https://example.org/audition")
        legacy_detail = opportunity_detail(item, legacy_application=True)
        self.assertEqual(legacy_detail["application"]["url"], "https://example.org/audition")
        self.assertEqual(legacy_detail["application"]["label"], "")

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
        self.assertEqual(opportunity_detail(item)["application"]["url"], item["listing_url"])
        self.assertEqual(opportunity_detail(item)["application"]["label"], "View source listing")
        self.assertEqual(opportunity_detail(item)["application"]["verification_status"], "source_only")
        self.assertEqual(opportunity_detail(item, legacy_application=True)["application"]["url"], "")

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
        self.assertEqual(opportunity_card(item)["track"], "agency")

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
