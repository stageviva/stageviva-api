"""Ready means auto-publish; genuine blockers and owner edits remain protected."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from opportunity_discovery import DiscoveredOpportunity
from opportunity_imports import ImportQueue, research_links
from opportunity_publication import prepare_publication, publication_reason
from opportunity_presentation import opportunity_detail
from opportunity_quality import application_pathway, confirm_editorial_review, supported_excerpt
from opportunity_intelligence import extract_useful_text, extract_official_application_url
from opportunity_imports import ImportInputError
from storage import merge_opportunity_updates
from opportunity_source_facts import complete_source_facts
from storage import StageVivaStorage
from test_opportunity_imports import ready, CAPTION


class PublicationPolicyTest(unittest.TestCase):
    def test_typography_differences_preserve_evidence_but_changed_facts_do_not(self):
        self.assertTrue(supported_excerpt("Company's dancers - full-time", "Company’s dancers – full-time"))
        self.assertFalse(supported_excerpt("No experience required", "Experience required"))
        self.assertFalse(supported_excerpt("Deadline 19 November 2026", "Deadline 18 November 2026"))

    def test_screenshot_style_standards_include_foundation_in_both_disciplines(self):
        result = complete_source_facts({}, "They are looking for versatile dance artists with a strong foundation in both classical ballet and contemporary techniques.")
        requirements = result["requirements"]["discipline_requirements"]
        self.assertEqual(requirements["classical_ballet"]["required_level"], "high")
        self.assertEqual(requirements["contemporary"]["required_level"], "high")

    def test_apply_button_and_mailto_destinations_survive_text_extraction(self):
        text = extract_useful_text('<main><a href="https://company.org/auditions/apply">Apply here</a><p>Send applications to <a href="mailto:casting@company.org?subject=Audition">the casting team</a></p></main>')
        self.assertIn("https://company.org/auditions/apply", text)
        self.assertIn("casting@company.org", text)
        self.assertNotIn("subject=Audition", text)

    def test_company_contract_research_does_not_pick_the_separate_youth_application(self):
        html = '<a href="https://forms.example.org/z-company">Read more about applying to the ballet here</a><a href="https://forms.example.org/a-youth">Read more about applying to the Young Dancers programme here</a>'
        self.assertEqual(extract_official_application_url(html, "https://company.org/", prefer_link=True, role_text="Ballet company dancers for full-time contracts"), "https://forms.example.org/z-company")
        self.assertEqual(extract_official_application_url(html, "https://company.org/", prefer_link=True, role_text="Young dancers youth programme audition"), "https://forms.example.org/a-youth")
        self.assertIn("https://forms.example.org/z-company", extract_useful_text(html))

    def test_official_website_fallback_survives_visibility_and_editor_confirmation(self):
        document = ready()
        document["stageviva_review"]["pathway"] = {"kind": "official_website", "target": "https://company.org/", "evidence": ["Company website"]}
        document["source"] = {"official_url": {"value": "https://company.org/"}}
        document["application"] = {"application_url": {"value": "unknown"}, "email": {"value": "unknown"}}
        merged = merge_opportunity_updates(document, {"visible": True})
        self.assertEqual(application_pathway(merged)["kind"], "official_website")
        confirm_editorial_review(merged)
        self.assertEqual(application_pathway(merged)["kind"], "official_website")
        changed = merge_opportunity_updates(document, {"official_url": "https://company.org/new-audition"})
        self.assertEqual(application_pathway(changed), {})
        approved = merge_opportunity_updates(document, {"official_url": "https://new-company.org/", "review_confirmed": True})
        self.assertEqual(application_pathway(approved), {"kind": "official_website", "target": "https://new-company.org/"})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = StageVivaStorage(str(Path(self.temp.name) / "test.db"))
        self.queue = ImportQueue(self.storage, Path(self.temp.name) / "images")

    def tearDown(self):
        self.storage.close()
        self.temp.cleanup()

    def held(self, document=None, number=1):
        document = copy.deepcopy(document or ready())
        url = f"https://www.instagram.com/p/Held{number}/"
        job = self.queue.enqueue("owner", f"held_request_{number}", url, CAPTION, True, [])
        document["stageviva_review"]["import_id"] = job["id"]
        oid = self.storage.upsert_opportunity(DiscoveredOpportunity("Cruise production dancers", url, "StageViva import", url, "manual"), document, hidden_reason="needs_verification")
        self.queue.update(job["id"], status="review", opportunity_id=oid, opportunity_json=json.dumps(document))
        return job["id"], oid

    def test_ai_ready_result_is_not_blocked_by_optional_missing_fields_or_secondary_website_error(self):
        document = ready()
        document["overall_confidence"] = "medium"
        document["stageviva_review"].update(ready_for_publication=True, blocking_reasons=[], research_error="An unrelated secondary page failed")
        self.assertIsNone(publication_reason(document))
        document["stageviva_review"].update(ready_for_publication=False, blocking_reasons=["critical_requirements_unclear"])
        self.assertIsNotNone(publication_reason(document))

    def test_previously_held_ready_import_publishes_once_without_provider_or_fetch(self):
        document = ready(); document["overall_confidence"] = "medium"
        document["stageviva_review"]["research_error"] = "Optional page blocked"
        jid, oid = self.held(document)
        with patch("opportunity_imports.analyse_opportunity") as ai, patch("opportunity_imports.fetch_public_html") as fetch:
            self.assertEqual(self.queue.reconcile_reviews(lambda _: True), 1)
            self.assertEqual(self.queue.reconcile_reviews(lambda _: True), 0)
            ai.assert_not_called(); fetch.assert_not_called()
        self.assertTrue(self.storage.get_opportunity_for_admin(oid)["visible"])
        self.assertEqual(self.queue.get(jid)["status"], "published", self.queue.get(jid)["message"])
        self.assertEqual(self.queue.get(jid)["matching_status"], "pending")

    def test_manual_hides_manual_edits_and_revoked_admin_access_are_preserved(self):
        for number, change in enumerate(("hide", "edit", "revoked"), 1):
            jid, oid = self.held(number=number)
            if change == "hide": self.storage.hide_opportunity(oid)
            if change == "edit": self.storage.update_opportunity_title(oid, "Owner edited this title")
            self.queue.reconcile_reviews(lambda _: change != "revoked")
            self.assertFalse(self.storage.get_opportunity_for_admin(oid)["visible"])
            if change == "edit": self.assertEqual(self.storage.get_opportunity_for_admin(oid)["title"], "Owner edited this title")

    def test_conflicts_and_duplicate_rows_are_never_auto_published(self):
        conflict = ready(); conflict["stageviva_review"]["conflicts"] = ["Different deadlines"]
        _, conflict_id = self.held(conflict, 1)
        _, first = self.held(number=2)
        _, second = self.held(number=3)
        self.assertEqual(self.queue.reconcile_reviews(lambda _: True), 1)
        self.assertFalse(self.storage.get_opportunity_for_admin(conflict_id)["visible"])
        self.assertEqual(sum(self.storage.get_opportunity_for_admin(oid)["visible"] for oid in (first, second)), 1)

    def test_literal_application_email_and_fetched_explicit_website_can_recover_missed_routes(self):
        document = ready(); document["stageviva_review"]["pathway"] = {}
        document["stageviva_review"]["sources"] = [{"url": "stageviva-upload://one", "type": "caption", "text": CAPTION}]
        repaired = prepare_publication(document)
        self.assertEqual(repaired["stageviva_review"]["pathway"]["target"], "casting@example.org")
        self.assertEqual(document["stageviva_review"]["pathway"], {})
        document["stageviva_review"]["sources"] = [{"url": "stageviva-upload://one", "type": "caption", "text": "Official website: https://company.org/"}, {"url": "https://company.org/", "type": "linked_page", "text": "Official company website"}]
        self.assertEqual(prepare_publication(document)["stageviva_review"]["pathway"]["kind"], "official_website")
        document["stageviva_review"]["sources"].pop()
        self.assertEqual(prepare_publication(document)["stageviva_review"]["pathway"], {})
        document["stageviva_review"]["sources"] = [{"url": "stageviva-upload://one", "type": "caption", "text": "Application deadline: 16 November 2026. For questions, contact tickets@example.org"}]
        self.assertEqual(prepare_publication(document)["stageviva_review"]["pathway"], {})

    def test_recovered_supplied_website_completes_readiness_without_model_blockers(self):
        document = ready()
        document["stageviva_review"].update(pathway={}, ready_for_publication=False, blocking_reasons=[], sources=[
            {"url": "https://company.org/ballet", "requested_url": "https://company.org/ballet", "type": "linked_page", "text": "Company ballet auditions. Applications are open."}])
        repaired = prepare_publication(document, "https://company.org/ballet")
        self.assertIsNone(publication_reason(repaired))
        self.assertTrue(repaired["stageviva_review"]["application_recovered_from_source"])
        self.assertFalse(document["stageviva_review"]["ready_for_publication"])
        for blockers in (["critical_requirements_unclear"], ["application_missing", "critical_requirements_unclear"]):
            document["stageviva_review"]["blocking_reasons"] = blockers
            self.assertIsNotNone(publication_reason(prepare_publication(document, "https://company.org/ballet")))
        document["stageviva_review"].update(blocking_reasons=[], sources=[])
        self.assertIsNotNone(publication_reason(prepare_publication(document, "https://company.org/ballet")))

    def test_saved_hold_recovers_website_and_publishes_without_ai(self):
        document = ready()
        document["stageviva_review"].update(pathway={}, ready_for_publication=False, blocking_reasons=[], review_policy="source-ready-v3", sources=[
            {"url": "https://company.org/ballet", "type": "linked_page", "text": "Company ballet auditions. Applications are open."}])
        jid, oid = self.held(document)
        self.queue.update(jid, reference_url="https://company.org/ballet")
        with patch("opportunity_imports.analyse_opportunity") as ai, patch("opportunity_imports.fetch_public_html") as fetch:
            self.assertEqual(self.queue.reconcile_reviews(lambda _: True), 1)
            self.assertEqual(self.queue.reconcile_reviews(lambda _: True), 0)
            ai.assert_not_called(); fetch.assert_not_called()
        self.assertTrue(self.storage.get_opportunity_for_admin(oid)["visible"])

    def test_missing_classification_quote_reuses_supported_recruitment_facts(self):
        quote = "They are looking for versatile dance artists with a strong foundation in both classical ballet and contemporary techniques."
        document = ready()
        for field in document["identity"].values():
            field["evidence"] = [quote]
        document["stageviva_review"].update(decision="NEEDS_VERIFICATION", extraction_decision="VALID_DANCER_OPPORTUNITY", evidence=[], rejected_decision_evidence=[], blocking_reasons=[], ready_for_publication=True, pathway={}, sources=[
            {"url": "stageviva-upload://one", "type": "caption", "text": quote},
            {"url": "https://company.org/ballet", "type": "linked_page", "text": "Official company auditions"}])
        repaired = prepare_publication(document, "https://company.org/ballet")
        self.assertIsNone(publication_reason(repaired))
        self.assertTrue(repaired["stageviva_review"]["role_evidence_recovered_from_fields"])
        self.assertEqual(document["stageviva_review"]["decision"], "NEEDS_VERIFICATION")
        for changes in ({"conflicts": ["Different deadlines"]}, {"blocking_reasons": ["critical_requirements_unclear"]}, {"rejected_decision_evidence": ["invented quote"]}, {"extraction_decision": "NEEDS_VERIFICATION"}):
            held = copy.deepcopy(document)
            held["stageviva_review"].update(changes)
            self.assertIsNotNone(publication_reason(prepare_publication(held, "https://company.org/ballet")))
        for unsupported in ("We offer classes for dancers", "They are hiring teachers", "They are looking for dancers: invented text"):
            held = copy.deepcopy(document)
            for field in held["identity"].values(): field["evidence"] = [unsupported]
            self.assertIsNotNone(publication_reason(prepare_publication(held, "https://company.org/ballet")))
        jid, oid = self.held(document)
        self.queue.update(jid, reference_url="https://company.org/ballet")
        with patch("opportunity_imports.analyse_opportunity") as ai, patch("opportunity_imports.fetch_public_html") as fetch:
            self.assertEqual(self.queue.reconcile_reviews(lambda _: True), 1)
            ai.assert_not_called(); fetch.assert_not_called()
        self.assertTrue(self.storage.get_opportunity_for_admin(oid)["visible"])

    def test_legacy_source_is_clickable_without_claiming_a_verified_apply_route(self):
        item = {"opportunity_id": "legacy", "listing_url": "https://balletplaces.com/auditions/example", "opportunity": {"identity": {"title": {"value": "Dancers"}}}, "match": {}}
        detail = opportunity_detail(item)
        self.assertEqual(detail["application"]["url"], item["listing_url"])
        self.assertEqual(detail["application"]["label"], "View source listing")
        self.assertEqual(detail["application"]["verification_status"], "source_only")
        self.assertEqual(opportunity_detail(item, legacy_application=True)["application"]["url"], "")

    def test_screenshot_website_without_scheme_is_researched_without_treating_email_as_url(self):
        self.assertEqual(research_links("Apply by email casting@example.org. Website: company.org/internship-\nauditions"), ["https://company.org/internship-auditions"])
        self.assertEqual(research_links("Email: casting@example.org\nCV in PDF"), [])

    def test_held_import_can_be_reanalysed_and_updates_same_record_without_duplicate(self):
        held = ready(); held["stageviva_review"]["pathway"] = {}
        jid, oid = self.held(held)
        self.queue.retry(jid, "owner", CAPTION + " Official website: https://company.org/auditions", True, "https://company.org/auditions")
        with patch("opportunity_imports.analyse_opportunity", return_value=ready()) as ai:
            self.queue.process(self.queue.claim(), lambda _: True)
        self.assertEqual(ai.call_count, 1)
        self.assertEqual(self.queue.get(jid)["status"], "published", self.queue.get(jid)["message"])
        self.assertEqual(self.queue.get(jid)["opportunity_id"], oid)
        self.assertEqual(self.storage.count("opportunities"), 1)
        self.assertTrue(self.storage.get_opportunity_for_admin(oid)["visible"])

    def test_reanalysis_can_remain_held_and_retry_again(self):
        held = ready(); held["stageviva_review"]["pathway"] = {}
        jid, oid = self.held(held)
        for _ in range(2):
            self.queue.retry(jid, "owner", "Incomplete source announcement without application details.", True)
            with patch("opportunity_imports.analyse_opportunity", return_value=held):
                self.queue.process(self.queue.claim(), lambda _: True)
            self.assertEqual(self.queue.get(jid)["status"], "review")
            self.assertFalse(self.storage.get_opportunity_for_admin(oid)["visible"])

    def test_reanalysis_preserves_manual_edits_before_and_during_the_analysis(self):
        jid, oid = self.held()
        self.storage.update_opportunity_title(oid, "Owner corrected title")
        with self.assertRaises(ImportInputError):
            self.queue.retry(jid, "owner", CAPTION, True)
        jid, oid = self.held(number=2)
        self.queue.retry(jid, "owner", CAPTION, True)
        def edited_during_analysis(*args, **kwargs):
            self.storage.update_opportunity_title(oid, "Owner corrected during analysis")
            return ready()
        with patch("opportunity_imports.analyse_opportunity", side_effect=edited_during_analysis):
            self.queue.process(self.queue.claim(), lambda _: True)
        self.assertEqual(self.storage.get_opportunity_for_admin(oid)["title"], "Owner corrected during analysis")
        self.assertFalse(self.storage.get_opportunity_for_admin(oid)["visible"])


if __name__ == "__main__": unittest.main()
