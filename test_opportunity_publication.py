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
from storage import StageVivaStorage
from test_opportunity_imports import ready, CAPTION


class PublicationPolicyTest(unittest.TestCase):
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
        self.assertEqual(self.queue.get(jid)["status"], "published")
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


if __name__ == "__main__": unittest.main()
