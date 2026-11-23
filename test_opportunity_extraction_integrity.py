"""Exercise generated copy, citations, screenshot coverage and publication offline."""

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from opportunity_evidence import numbered_sources, resolve_citations
from opportunity_intelligence import analyse_opportunity, extract_useful_text
from opportunity_publication import prepare_publication, publication_reason
from opportunity_quality import VALID, application_pathway, apply_source_evidence
from opportunity_screenshots import read_screenshot, screenshot_regions
from opportunity_source_facts import complete_source_facts
from test_opportunity_overhaul import blank
from opportunity_schema import OPPORTUNITY_SCHEMA
from test_opportunity_imports import ready


ANNOUNCEMENT = """Example Ballet is looking for dancers to join the company for the 2027/28 season.
Looking for versatile dance artists with a strong foundation in both classical ballet and contemporary techniques.
Location: Stockholm, Sweden.
Apply before: 7 December 2026.
Full-Time Contracts
1 July 2027 – 30 June 2028
Website: https://company.org/auditions
"""


class ExtractionIntegrityTest(unittest.TestCase):
    def test_numbered_sources_preserve_every_word_without_fragmenting_the_prompt(self):
        text, bounded = numbered_sources([{"url": "one", "text": "x\n" * 22000}])
        self.assertLess(len(bounded), 900)
        self.assertEqual(sum(quote.count("x") for token, quote in bounded.items() if not token.endswith(":L1]]")), 22000)

    def extract(self, bad_title_reference=False):
        def response(**request):
            self.assertEqual(request["text"]["format"]["schema"], OPPORTUNITY_SCHEMA)
            passages = dict(re.findall(r"(\[\[S\d+:L\d+\]\]) ([^\n]+)", request["input"]))
            def cite(phrase):
                return next(key for key, text in passages.items() if phrase in text)
            def fact(value, phrase):
                return {"value": value, "evidence": [cite(phrase)], "confidence": "high"}
            raw = blank(OPPORTUNITY_SCHEMA)
            raw["identity"]["title"] = fact("Ballet company dancers", "is looking for dancers")
            if bad_title_reference:
                raw["identity"]["title"]["evidence"] = ["[[S1:L99999]]"]
            raw["identity"]["organisation"] = fact("Example Ballet", "is looking for dancers")
            raw["identity"]["description"] = fact("Full-time company dancers with strong classical ballet and contemporary technique.", "strong foundation")
            raw["identity"]["opportunity_type"] = fact("Company employment", "is looking for dancers")
            raw["location"]["city"] = fact("Stockholm", "Location: Stockholm")
            raw["location"]["country"] = fact("Sweden", "Location: Stockholm")
            raw["classification"].update(decision=VALID, evidence=[cite("is looking for dancers")],
                ready_for_publication=True, blocking_reasons=[], application_kind="direct_form",
                application_target="https://forms.example.org/company-2027",
                application_evidence=[cite("Apply for company contracts")])
            return Mock(output_text=json.dumps(raw))
        client = Mock(); client.responses.create.side_effect = response
        pages = [('<main>Example Ballet audition. <a href="https://forms.example.org/company-2027">Apply for company contracts</a></main>', "https://company.org/auditions"),
                 ('<main>Example Ballet company application 2027/28. Apply here.</main>', "https://forms.example.org/company-2027")]
        with patch("opportunity_intelligence.reserve_ai_request"), patch("opportunity_intelligence.fetch_public_html", side_effect=pages):
            result = analyse_opportunity("stageviva-upload://fixture", listing_text=ANNOUNCEMENT, research_urls=["https://company.org/auditions"], client=client)
        client.responses.create.assert_called_once()
        return result

    def test_generated_title_and_description_survive_source_reference_validation(self):
        result = self.extract()
        self.assertEqual(result["identity"]["title"]["value"], "Ballet company dancers")
        self.assertNotIn("[[S", str(result["identity"]["title"]["evidence"]))
        self.assertEqual(result["dates"]["application_deadline"]["value"], "2026-12-07")
        self.assertEqual(result["dates"]["contract_start"]["value"], "2027-07-01")
        self.assertEqual(result["dates"]["contract_end"]["value"], "2028-06-30")
        self.assertEqual(result["requirements"]["discipline_requirements"]["classical_ballet"]["required_level"], "high")
        self.assertEqual(result["requirements"]["discipline_requirements"]["contemporary"]["required_level"], "high")
        self.assertEqual(result["requirements"]["styles"]["value"], ["Classical ballet", "Contemporary"])
        self.assertEqual(application_pathway(result)["target"], "https://forms.example.org/company-2027")
        self.assertIsNone(publication_reason(result))

    def test_invented_citation_is_rejected_and_diagnosable(self):
        result = self.extract(bad_title_reference=True)
        self.assertEqual(result["identity"]["title"]["value"], "unknown")
        self.assertIsNotNone(publication_reason(result))
        self.assertEqual(result["stageviva_review"]["citation_problems"][0]["reason"], "unknown_source_reference")
        self.assertEqual(result["stageviva_review"]["rejected_fields"][0]["proposed_value"], "Ballet company dancers")

    def test_citation_ids_are_scoped_to_each_source_and_legacy_quotes_still_work(self):
        _, references = numbered_sources([{"url": "one", "text": "First fact"}, {"url": "two", "text": "Different fact"}])
        document = {"title": {"value": "Generated", "evidence": ["[[S2:L2]]"], "confidence": "high"},
                    "description": {"value": "Legacy", "evidence": ["First fact"], "confidence": "high"}}
        resolve_citations(document, references)
        self.assertEqual(document["title"]["evidence"], ["Different fact"])
        self.assertFalse(apply_source_evidence(document, "First fact\nDifferent fact"))

    def test_recovered_application_clears_only_its_resolved_blocker(self):
        for blockers in (["application_missing"], ["application_missing", "critical_requirements_unclear"]):
            document = ready()
            document["stageviva_review"].update(ready_for_publication=False, blocking_reasons=list(blockers))
            fixed = prepare_publication(document)
            self.assertNotIn("application_missing", fixed["stageviva_review"]["blocking_reasons"])
            self.assertEqual(fixed["stageviva_review"]["ready_for_publication"], len(blockers) == 1)
            self.assertEqual(document["stageviva_review"]["blocking_reasons"], blockers)

    def test_audition_ranges_are_not_contract_dates_and_missing_years_stay_unknown(self):
        recovered = complete_source_facts({}, "Full-Time Contracts\ni 1 July 2027 - 30 June 2028")
        self.assertEqual(recovered["dates"]["contract_start"]["value"], "2027-07-01")
        for text in ("Audition: 27 February 2027 – 28 February 2027", "Contracts: 1 July – 30 June", "Contract season 2027/28"):
            self.assertNotIn("contract_start", complete_source_facts({}, text).get("dates", {}))
        conflicting = complete_source_facts({}, "Contracts: 1 July 2027 – 30 June 2028. Contracts: 1 August 2027 – 30 June 2028.")
        self.assertTrue(conflicting["stageviva_review"]["conflicts"])

    def test_recovered_styles_preserve_existing_styles_without_duplicate_ballet(self):
        original = {"requirements": {"styles": {"value": ["Ballet", "Jazz"], "evidence": ["Jazz required"], "confidence": "high"}}}
        recovered = complete_source_facts(original, ANNOUNCEMENT)
        self.assertEqual(recovered["requirements"]["styles"]["value"], ["Ballet", "Jazz", "Contemporary"])
        self.assertEqual(original["requirements"]["styles"]["value"], ["Ballet", "Jazz"])

    def test_main_article_is_kept_without_account_modal_noise(self):
        notice = "Example Ballet seeks company dancers. Applications close 7 December 2026. Strong classical ballet required."
        text = extract_useful_text(f'<title>Company auditions</title><main>{notice}</main><div role="dialog">Create account. Password reset. Your reservation expired.</div>')
        self.assertIn(notice, text)
        self.assertNotIn("Password reset", text)


class ScreenshotCoverageTest(unittest.TestCase):
    def test_three_overlapping_bands_cover_every_row_including_the_bottom(self):
        for height in (200, 2796, 10001):
            regions = screenshot_regions(738, height)
            self.assertLessEqual(len(regions), 3)
            self.assertEqual(regions[0][1], 0)
            self.assertEqual(regions[-1][3], height)
            self.assertTrue(all(first[3] >= second[1] for first, second in zip(regions, regions[1:])))

    def test_band_read_recovers_bottom_dates_and_inverts_dark_screenshots(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "caption.png"
            Image.new("RGB", (400, 900), "black").save(path)
            observed = []
            def band_reader(image, **options):
                observed.append(image.getpixel((0, 0)))
                return "1 July 2027 – 30 June 2028" if len(observed) == 3 else "Company dancer announcement"
            with patch("opportunity_screenshots._primary_reader", return_value="Full-Time Contracts"), patch("pytesseract.image_to_string", side_effect=band_reader) as reader:
                text = read_screenshot(path, 12000, timeout=20)
            self.assertIn("1 July 2027 – 30 June 2028", text)
            self.assertEqual(observed, [255, 255, 255])
            self.assertEqual(reader.call_count, 3)

    def test_long_source_is_rejected_instead_of_silently_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "caption.png"
            Image.new("RGB", (400, 900), "white").save(path)
            with patch("opportunity_screenshots._primary_reader", return_value="A" * 200), patch("pytesseract.image_to_string", return_value=""):
                with self.assertRaises(ValueError):
                    read_screenshot(path, 100)

    def test_partial_ocr_timeout_does_not_masquerade_as_a_complete_screenshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "caption.png"
            Image.new("RGB", (400, 900), "white").save(path)
            with patch("opportunity_screenshots._primary_reader", side_effect=RuntimeError("timeout")), patch("pytesseract.image_to_string", side_effect=["Header", RuntimeError("timeout"), "Deadline"]):
                with self.assertRaisesRegex(ValueError, "complete screenshot"):
                    read_screenshot(path, 12000)
