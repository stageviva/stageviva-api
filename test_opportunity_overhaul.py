"""Source-backed and synthetic regression checks. Providers and transport are isolated."""

from datetime import date
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from catalogue_audit import SARAJEVO_LISTING, SARAJEVO_SOURCE, audit
from match_engine import match_input_signature, reapply_deterministic_match_guards
from opportunity_discovery import DiscoveredOpportunity
from opportunity_intelligence import analyse_opportunity, extract_official_application_url
from opportunity_lifecycle import is_current_opportunity, opportunity_deadline
from opportunity_presentation import opportunity_detail
from opportunity_quality import INVALID, REVIEW, VALID, review_reason
from opportunity_schema import OPPORTUNITY_SCHEMA
from public_source import PublicSourceError, fetch_public_html
from source_registry import Source
from stageviva_pipeline import run_pipeline
from storage import StageVivaStorage, _same_live_opportunity


def blank(schema):
    kind = schema["type"]
    if kind == "object":
        return {key: blank(child) for key, child in schema["properties"].items()}
    if kind == "array":
        return []
    if kind == "boolean":
        return False
    return "unknown" if "unknown" in schema.get("enum", ["unknown"]) else schema["enum"][0]


def field(value, evidence):
    return {"value": value, "evidence": [evidence], "confidence": "high"}


def extract(text, decision, *, target="", kind="unverified", source_url="https://example.org/casting/round-2", quote=None, title="Company Dancer"):
    raw = blank(OPPORTUNITY_SCHEMA)
    raw["identity"]["title"] = field(title, text)
    raw["identity"]["organisation"] = field("Example Company", text)
    raw["classification"].update(
        decision=decision, selection_outcome="Advertised selection outcome", evidence=[quote or text],
        application_kind=kind, application_target=target, application_evidence=[quote or text],
    )
    client = Mock()
    client.responses.create.return_value.output_text = json.dumps(raw)
    with patch("opportunity_intelligence.fetch_public_html", return_value=(f"<main>{text}</main>", source_url)), \
         patch("opportunity_intelligence.reserve_ai_request"):
        return analyse_opportunity(source_url, client=client)


class SourceDecisionRegressionTest(unittest.TestCase):
    def test_named_sarajevo_case_rejects_ad_and_uses_theatre_email(self):
        html = '<aside><a href="https://artcasse.com/shop?aff=partner">Shop dancewear</a></aside>'
        self.assertEqual(extract_official_application_url(html, SARAJEVO_LISTING), "")
        text = "Male corps de ballet recruitment. Apply by email to balet@nps.ba."
        role = extract(text, VALID, target="balet@nps.ba", kind="email", source_url=SARAJEVO_SOURCE)
        self.assertIsNone(review_reason(role))
        detail = opportunity_detail({"opportunity_id": "sarajevo", "opportunity": role, "match": {}, "listing_url": SARAJEVO_LISTING})
        self.assertEqual(detail["application"]["label"], "Apply by Email")
        self.assertEqual(detail["application"]["email"], "balet@nps.ba")
        self.assertEqual(detail["application"]["url"], "")
        self.assertEqual(detail["deadline"], "")

    def test_labelled_offers_follow_the_selection_outcome(self):
        # Short paraphrases of official notices plus independent synthetic edge cases.
        # This exercises validation and policy with a fixed model response, not AI accuracy.
        cases = (
            ("Russian Masters intensive admission and scholarships", INVALID),
            ("Rehearsal director wanted with extensive professional ballet experience", INVALID),
            ("Recruiting dancers who will work with the rehearsal director", VALID),
            ("Resort entertainment shows recruiting dancers alongside vocalists", VALID),
            ("Ballet school admissions leading to a diploma", INVALID),
            ("Peridance company and trainee performance contracts", VALID),
            ("Auditions to join a summer ballet intensive as a student", INVALID),
            ("Royal Caribbean dancers for signature production shows", VALID),
            ("Mamma Mia stage ensemble recruitment requiring professional dance and singing", VALID),
            ("Shanghai Disney musical theatre vocalist casting with movement skills", INVALID),
            ("Jacob Jonas casting dancers for commercial and company projects", VALID),
            ("Ori Nation film invites dancers to apply for the cast", VALID),
            ("Company seeks performers for unpaid contemporary repertoire project", VALID),
            ("News story recounts an audition completed years ago", INVALID),
            ("Announcement lists a date and contact email but offers no role", INVALID),
            ("Application invited for dancer role, pay and deadline unspecified", VALID),
            ("Ambiguous graduate opportunity with no defined outcome", REVIEW),
        )
        target = "https://example.org/casting/round-2"
        for text, decision in cases:
            with self.subTest(text=text):
                role = extract(text, decision, target=target, kind="official_announcement")
                expected = None if decision == VALID else "non_dancer_opportunity" if decision == INVALID else "needs_verification"
                self.assertEqual(review_reason(role), expected)

    def test_unrelated_target_and_unsupported_excerpts_go_to_review(self):
        role = extract("Dancer applications open for the spring production", VALID,
                       target="https://example.org/casting/round-2", kind="relevant_listing")
        self.assertIsNone(review_reason(role))
        role = extract("Dancer applications open for the spring production", VALID,
                       target="https://example.org/login", kind="direct_form", source_url="https://example.org/login")
        self.assertEqual(review_reason(role), "unverified_application")
        role = extract("Dancer applications open for the spring production", VALID,
                       target="https://example.org/shop/apply", kind="direct_form")
        self.assertEqual(review_reason(role), "unverified_application")
        role = extract("Season announcement", VALID, quote="Invented audition notice")
        self.assertEqual(review_reason(role), "needs_verification")

    def test_incomplete_calendar_text_never_invents_a_deadline(self):
        for value in ("23 August", "May 2026", "23, 2026", "ASAP", "unknown"):
            role = {"dates": {"application_deadline": {"value": value}}}
            self.assertIsNone(opportunity_deadline(role))
            self.assertTrue(is_current_opportunity(role, today=date(2026, 11, 6)))
        self.assertFalse(is_current_opportunity({"status": {"value": "closed"}}))

    def test_pipeline_retains_ambiguous_evidence_without_matching_or_publishing(self):
        source = Source("Test Source", "https://example.org/casting", "ballet_dance")
        item = DiscoveredOpportunity("Company dancer", "https://example.org/casting/round-2", source.name, source.url, source.category)
        role = extract("Unclear dancer announcement", REVIEW)
        with tempfile.TemporaryDirectory() as directory:
            storage = StageVivaStorage(Path(directory) / "stageviva.db")
            try:
                result = run_pipeline(source, [{"identity": {"name": "Fixture dancer"}}], storage,
                                      discover=lambda _: [item], analyse=lambda *_: role, match=Mock(side_effect=AssertionError))
                self.assertEqual(result.stored_opportunities, 0)
                row = storage.list_all_opportunities()[0]
                self.assertEqual(row["hidden_reason"], "needs_verification")
                self.assertIn("Unclear dancer announcement", row["opportunity"]["stageviva_review"]["sources"][0]["text"])
                self.assertEqual(storage.count("match_results"), 0)
                self.assertEqual(audit(Path(directory) / "stageviva.db")["stats"]["total"], 1)
            finally:
                storage.close()

    def test_same_application_form_does_not_collapse_distinct_roles_or_rounds(self):
        def item(title, deadline):
            return {"title": title, "source_name": "Company", "listing_url": "https://example.org/listing/" + title.replace(" ", "-"),
                    "opportunity": {"identity": {"title": {"value": title}, "organisation": {"value": "Example Ballet"}},
                                    "application": {"application_url": {"value": "https://example.org/application/form"}},
                                    "dates": {"application_deadline": {"value": deadline}}}}
        corps = item("Male corps de ballet", "2027-01-03")
        self.assertFalse(_same_live_opportunity(corps, item("Female soloist", "2027-01-03")))
        self.assertFalse(_same_live_opportunity(corps, item("Male corps de ballet", "2028-01-03")))
        self.assertTrue(_same_live_opportunity(corps, item("Male corps de ballet", "2027-01-03")))


class EvidenceMatchRegressionTest(unittest.TestCase):
    def test_mandatory_height_conflict_and_unknown_are_different(self):
        role = {"requirements": {"physical": {"height_requirement": {"value": "Minimum height: 180 cm"}}}}
        model = {"overall": {"match_score": 84, "match_level": "strong"},
                 "match_reasons": [{"reason": "Classical ballet experience", "evidence": ["Classical ballet"]}]}
        short = reapply_deterministic_match_guards({"physical": {"height": "176 cm"}}, role, model)
        tall = reapply_deterministic_match_guards({"physical": {"height": "183 cm"}}, role, model)
        unknown = reapply_deterministic_match_guards({}, role, model)
        self.assertEqual(short["stageviva_assessment"]["eligibility"], "ineligible")
        self.assertLess(short["overall"]["match_score"], tall["overall"]["match_score"])
        self.assertEqual(unknown["stageviva_assessment"]["height"], "unknown")
        self.assertEqual(unknown["overall"]["match_score"], tall["overall"]["match_score"])
        self.assertIn("176 cm", short["stageviva_assessment"]["warning"])

    def test_score_unavailable_without_evidence_and_signature_ignores_source_snapshot(self):
        match = reapply_deterministic_match_guards({}, {}, {"overall": {"match_score": 90}})
        item = {"opportunity_id": "x", "opportunity": {}, "match": match}
        self.assertIsNone(opportunity_detail(item)["match"]["score"])
        self.assertEqual(match_input_signature({}, {}), match_input_signature({}, {"stageviva_review": {"sources": ["snapshot"]}}))
        self.assertNotEqual(match_input_signature({}, {}), match_input_signature({"physical": {"height": "180 cm"}}, {}))


class PublicFetchRegressionTest(unittest.TestCase):
    def test_dns_and_redirects_cannot_reach_internal_addresses(self):
        response = Mock(status=302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})
        with patch("public_source.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443))]), \
             patch("public_source.urllib3.HTTPSConnectionPool") as pool:
            pool.return_value.urlopen.return_value = response
            with self.assertRaises(PublicSourceError):
                fetch_public_html("https://example.org/audition", {})
            self.assertEqual(pool.call_args.args[0], "93.184.216.34")
            self.assertEqual(pool.call_args.kwargs["assert_hostname"], "example.org")
            pool.return_value.urlopen.assert_called_once()
        with patch("public_source.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443))]):
            with self.assertRaisesRegex(PublicSourceError, "non-public address"):
                fetch_public_html("https://example.org/audition", {})
