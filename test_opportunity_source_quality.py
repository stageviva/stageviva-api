"""Regression fixture transcribed from the founder's supplied screenshot.

No paid providers, live site fetches, or production data are used here.
"""
import copy
import json
import unittest
from unittest.mock import Mock, patch

from match_engine import reapply_deterministic_match_guards
from opportunity_imports import research_links
from opportunity_intelligence import analyse_opportunity
from opportunity_lifecycle import opportunity_deadline
from opportunity_presentation import opportunity_detail
from opportunity_quality import VALID, REVIEW, application_pathway, supported_excerpt
from opportunity_source_facts import complete_source_facts
from public_source import PublicSourceError
from test_opportunity_overhaul import blank, field
from opportunity_schema import OPPORTUNITY_SCHEMA

TEXT = """Scapino Ballet Rotterdam is looking for Interns.
Contemporary dance internship for the 2027-2028 season.
REQUIREMENTS:
Daring and passionate dance students (M/F/X) with excellent
classical and contemporary technique, a strong creative
signature, refined artistry and improvisational playfulness.
Still enrolled in school for the season, graduating in June 2028.
INTERNSHIP:
One full season, 2027-2028. Monthly compensation of EUR375.
Scapino does not arrange or cover housing.
AUDITION: 5 December 2026 (invitation only), Rotterdam.
HOW TO APPLY:
Send your application by email to: interns@scapinoballet.nl
APPLICATION MATERIAL: CV in PDF. Recent headshot. Video, max. 3 minutes.
DEADLINE:
16 November 2026
Pre-selection results via email on 27 November 2026.
Website: https://www.scapinoballet.nl/en/internship-
auditions
"""
WEBSITE = "https://www.scapinoballet.nl/en/internship-auditions"


def stored():
    return {"identity": {"title": {"value": "Contemporary dance interns"}, "organisation": {"value": "Scapino Ballet Rotterdam"}, "opportunity_type": {"value": "Company internship"}},
            "stageviva_review": {"decision": VALID, "sources": [{"text": TEXT, "type": "caption", "url": "stageviva-upload://fixture"}],
                                 "pathway": {"kind": "email", "target": "interns@scapinoballet.nl"}}, "overall_confidence": "high"}


class SourceQualityTest(unittest.TestCase):
    def test_saved_source_recovers_deadline_levels_and_student_condition_without_mutation(self):
        original = stored()
        snapshot = copy.deepcopy(original)
        result = complete_source_facts(original)
        self.assertEqual(result["dates"]["application_deadline"]["value"], "2026-11-16")
        for name in ("classical_ballet", "contemporary"):
            requirement = result["requirements"]["discipline_requirements"][name]
            self.assertEqual(requirement["required_level"], "high")
            self.assertTrue(supported_excerpt(requirement["evidence"][0], TEXT))
        criteria = [item["value"] for item in result["requirements"]["experience"]["criteria"]]
        self.assertTrue(any("graduating in June 2028" in item for item in criteria))
        self.assertTrue(any("Refined artistry" in item for item in criteria))
        self.assertEqual(original, snapshot)
        self.assertEqual(opportunity_deadline(original).isoformat(), "2026-11-16")

    def test_detail_displays_technical_levels_and_deadline_from_cached_source(self):
        detail = opportunity_detail({"opportunity_id": "fixture", "listing_url": "", "opportunity": stored(), "match": {}})
        self.assertEqual(detail["deadline"], "2026-11-16")
        self.assertIn("Advanced classical ballet technique", detail["requirements"]["summary"])
        self.assertIn("Advanced contemporary technique", detail["requirements"]["summary"])
        self.assertEqual(detail["application"]["email"], "interns@scapinoballet.nl")
        self.assertEqual(detail["track"], "apprenticeship")

    def test_wrapped_link_preserves_real_hyphen_and_ignores_non_url_text(self):
        self.assertEqual(research_links(TEXT), [WEBSITE])
        self.assertEqual(research_links("Website: https://company.org/\napply\nOther notes"), ["https://company.org/apply"])
        self.assertEqual(research_links("https://127.0.0.1/\nsecret"), [])

    def test_no_year_is_invented_and_audition_date_is_not_a_deadline(self):
        for text in ("Audition: 5 December 2026", "Deadline: 16 November", "Contract season 2027-2028"):
            result = complete_source_facts({}, text)
            self.assertFalse(result.get("dates", {}).get("application_deadline"))

    def test_conflicting_deadlines_and_preferred_or_alternative_skills_remain_uncertain(self):
        result = complete_source_facts({}, "Deadline: 16 November 2026. Deadline: 20 November 2026.")
        self.assertNotIn("application_deadline", result.get("dates", {}))
        self.assertTrue(result["stageviva_review"]["conflicts"])
        for text in ("Requirements: advanced ballet or contemporary technique.", "Requirements: excellent ballet technique preferred.", "We do not require advanced ballet technique."):
            self.assertFalse(complete_source_facts({}, text).get("requirements", {}).get("discipline_requirements"))

    def test_known_matching_level_and_current_enrolment_can_still_be_a_strong_match(self):
        artist = {"disciplines": [{"discipline": name, "level": "high", "confidence": "high"} for name in ("classical_ballet", "contemporary")],
                  "training": [{"status": "ongoing", "end_date": "June 2028"}]}
        model = {"overall": {"match_score": 95, "match_level": "strong", "recommendation": "strong_match"}, "match_reasons": [{"evidence": ["Training"], "reason": "Training"}]}
        strong = reapply_deterministic_match_guards(artist, stored(), model)
        self.assertEqual(strong["overall"]["match_score"], 95)
        lower = copy.deepcopy(artist); lower["disciplines"][0]["level"] = "medium"
        result = reapply_deterministic_match_guards(lower, stored(), model)
        self.assertLessEqual(result["overall"]["match_score"], 65)
        self.assertNotEqual(result["overall"]["recommendation"], "strong_match")
        self.assertTrue(any(item["factor"] == "Required technique level" for item in result["gaps"]))
        mention_only = {"disciplines": [{"discipline": "classical_ballet", "level": "high", "confidence": "unknown"}]}
        self.assertLessEqual(reapply_deterministic_match_guards(mention_only, stored(), model)["overall"]["match_score"], 55)
        no_enrolment = copy.deepcopy(artist); no_enrolment["training"] = []
        self.assertLessEqual(reapply_deterministic_match_guards(no_enrolment, stored(), model)["overall"]["match_score"], 65)
        self.assertEqual(reapply_deterministic_match_guards(artist, stored(), result)["overall"]["match_score"], 95)

    def test_actual_extractor_recovers_omitted_fields_with_a_fixed_provider_response(self):
        raw = blank(OPPORTUNITY_SCHEMA)
        raw["identity"]["title"] = field("Contemporary dance interns", "looking for Interns")
        raw["identity"]["organisation"] = field("Scapino Ballet Rotterdam", "Scapino Ballet Rotterdam")
        raw["classification"].update(decision=VALID, evidence=["looking for Interns"], application_kind="email", application_target="interns@scapinoballet.nl", application_evidence=["Send your application by email to: interns@scapinoballet.nl"])
        client = Mock(); client.responses.create.return_value.output_text = json.dumps(raw)
        with patch("opportunity_intelligence.reserve_ai_request"):
            result = analyse_opportunity("stageviva-upload://fixture", listing_text=TEXT, client=client)
        self.assertEqual(result["dates"]["application_deadline"]["value"], "2026-11-16")
        self.assertEqual(result["requirements"]["discipline_requirements"]["contemporary"]["required_level"], "high")

    def test_research_can_follow_one_application_link_but_never_exceed_two_pages(self):
        raw = blank(OPPORTUNITY_SCHEMA)
        client = Mock(); client.responses.create.return_value.output_text = json.dumps(raw)
        page = '<main>Official company site <a href="/apply/audition">Apply for the audition</a></main>'
        with patch("opportunity_intelligence.reserve_ai_request"), patch("opportunity_intelligence.fetch_public_html", side_effect=[(page, "https://company.org/"), ("<main>Application instructions.</main>", "https://company.org/apply/audition")]) as fetch:
            analyse_opportunity("stageviva-upload://fixture", listing_text="See the official website for audition information.", research_urls=["https://company.org/"], client=client)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args_list[1].args[0], "https://company.org/apply/audition")

    def test_homepage_fallback_is_labelled_visit_website_and_never_claims_apply_now(self):
        role = stored()
        role["stageviva_review"]["pathway"] = {"kind": "official_website", "target": "https://company.org/"}
        self.assertTrue(application_pathway(role))
        detail = opportunity_detail({"opportunity_id": "fixture", "listing_url": "", "opportunity": role, "match": {}})
        self.assertEqual(detail["application"]["label"], "Visit official website")
        for url in ("https://127.0.0.1/", "https://company.org/login", "https://instagram.com/company", "https://allcasting.com/", "https://company.org/?aff=partner", "https://company.org/privacy"):
            role["stageviva_review"]["pathway"]["target"] = url
            self.assertFalse(application_pathway(role))

    def test_extractor_website_fallback_requires_a_fetched_official_destination(self):
        text = "Example Company seeks contemporary dancers. Official website: https://company.org/"
        raw = blank(OPPORTUNITY_SCHEMA)
        raw["identity"]["title"] = field("Contemporary dancers", "contemporary dancers")
        raw["identity"]["organisation"] = field("Example Company", "Example Company")
        raw["classification"].update(decision=VALID, evidence=["seeks contemporary dancers"], application_kind="official_website", application_target="https://company.org/", application_evidence=["Official website: https://company.org/"])
        client = Mock(); client.responses.create.return_value.output_text = json.dumps(raw)
        with patch("opportunity_intelligence.reserve_ai_request"), patch("opportunity_intelligence.fetch_public_html", return_value=("<main>Example Company.</main>", "https://company.org/")):
            result = analyse_opportunity("stageviva-upload://fixture", listing_text=text, research_urls=["https://company.org/"], client=client)
        self.assertEqual(application_pathway(result)["kind"], "official_website")
        with patch("opportunity_intelligence.reserve_ai_request"), patch("opportunity_intelligence.fetch_public_html", side_effect=PublicSourceError("Blocked")):
            blocked = analyse_opportunity("stageviva-upload://fixture", listing_text=text, research_urls=["https://company.org/"], client=client)
        self.assertFalse(application_pathway(blocked))


if __name__ == "__main__": unittest.main()
