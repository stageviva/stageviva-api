"""Synthetic importer checks; no live providers, source reads or paid analyses."""

import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from fastapi.testclient import TestClient

import api
from ai_safety import AIRequestsDisabledError
from opportunity_discovery import DiscoveredOpportunity
from opportunity_imports import ImportInputError, ImportQueue, canonical_source, research_links
from opportunity_quality import VALID
from public_source import PublicSourceError
from storage import StageVivaStorage


URL = "https://www.instagram.com/p/AbCd_123/"
CAPTION = "Cruise company seeks production dancers for an onboard performing contract. Apply with your CV to casting@example.org."


def ready():
    field = lambda value: {"value": value, "evidence": [CAPTION], "confidence": "high"}
    return {
        "identity": {"title": field("Cruise production dancers"), "organisation": field("Example Cruises"), "opportunity_type": field("Performing contract")},
        "location": {"city": field("London"), "country": field("United Kingdom")},
        "dates": {"application_deadline": field("2099-12-31")},
        "overall_confidence": "high",
        "stageviva_review": {"decision": VALID, "evidence": [CAPTION], "conflicts": [], "research_error": None,
                             "pathway": {"kind": "email", "target": "casting@example.org", "evidence": [CAPTION]}},
    }


def screenshot():
    output = io.BytesIO()
    Image.new("RGB", (300, 400), "white").save(output, format="PNG")
    return output.getvalue()


class ImportQueueTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.storage = StageVivaStorage(str(self.path / "test.db"))
        self.queue = ImportQueue(self.storage, self.path / "private")
        self.ai = patch("opportunity_imports.analyse_opportunity", return_value=ready()).start()
        self.fetch = patch("opportunity_imports.fetch_public_html", side_effect=AssertionError("Unexpected network read")).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        self.storage.close()
        self.directory.cleanup()

    def enqueue(self, **updates):
        data = dict(owner_id="owner", request_id="request_123", url=URL, caption=CAPTION, complete=False, images=[])
        data.update(updates)
        return self.queue.enqueue(**data)

    def process(self):
        job = self.queue.claim()
        self.assertIsNotNone(job)
        self.queue.process(job, lambda owner: owner == "owner")
        return self.queue.get(job["id"])

    def test_canonical_instagram_preserves_case_and_removes_tracking(self):
        self.assertEqual(canonical_source("http://instagram.com/p/AbCd_123/?igsh=token"), URL)
        for url in ("https://instagram.com/account", "https://127.0.0.1/job", "https://user:password@example.org/job", "file:///tmp/job"):
            with self.assertRaises(ImportInputError): canonical_source(url)

    def test_upload_idempotence_is_durable_and_content_bound(self):
        first = self.enqueue(images=[screenshot()])
        self.assertEqual(self.enqueue(images=[screenshot()])["id"], first["id"])
        with self.assertRaises(ImportInputError): self.enqueue(caption="Different")
        self.assertEqual(len(list((self.path / "private").iterdir())), 1)
        reopened = StageVivaStorage(str(self.path / "test.db"))
        try:
            self.assertEqual(ImportQueue(reopened, self.path / "private").list("owner")[0]["id"], first["id"])
        finally: reopened.close()
        self.ai.assert_not_called()

    def test_complete_verified_source_publishes_once_and_queues_matching(self):
        job = self.enqueue()
        result = self.process()
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["matching_status"], "pending")
        self.assertTrue(self.storage.opportunity_by_listing_url(URL)["visible"])
        self.assertEqual(self.queue.get(job["id"])["source_kind"], "caption")
        self.fetch.assert_not_called()
        self.ai.assert_called_once()

    def test_instagram_url_without_caption_needs_information_without_ai(self):
        self.enqueue(caption="")
        result = self.process()
        self.assertEqual(result["status"], "needs_information")
        self.ai.assert_not_called()
        self.fetch.assert_not_called()

    def test_screenshots_need_complete_confirmation_or_caption(self):
        self.enqueue(caption="", images=[screenshot()])
        with patch("opportunity_imports._extract_image_text", return_value=CAPTION):
            self.assertEqual(self.process()["status"], "needs_information")
        self.ai.assert_not_called()

    def test_complete_screenshots_are_read_locally_and_links_researched(self):
        self.enqueue(caption="", complete=True, images=[screenshot()])
        with patch("opportunity_imports._extract_image_text", return_value=CAPTION + " https://example.org/apply") as ocr:
            self.assertEqual(self.process()["status"], "published")
        self.assertEqual(ocr.call_args.kwargs["timeout"], 20)
        self.assertEqual(self.ai.call_args.kwargs["research_urls"], ["https://example.org/apply"])

    def test_unreadable_image_never_publishes_even_with_caption(self):
        self.enqueue(images=[screenshot()])
        with patch("opportunity_imports._extract_image_text", side_effect=RuntimeError("OCR unavailable")):
            self.assertEqual(self.process()["status"], "needs_information")
        self.ai.assert_not_called()

    def test_bad_and_oversized_screenshots_rejected_before_queueing(self):
        for images in ([b"not an image"], [screenshot()] * 6, [b"x" * (8 * 1024 * 1024 + 1)]):
            with self.assertRaises(ImportInputError): self.enqueue(images=images)
        self.assertEqual(self.queue.list("owner"), [])

    def test_unverified_conflicting_low_confidence_expired_and_training_stay_hidden(self):
        variants = []
        for change in ({"decision": "NEEDS_VERIFICATION"}, {"conflicts": ["Conflicting dates"]}, {"research_error": "Blocked page"}, {"pathway": {}}):
            item = ready(); item["stageviva_review"].update(change); variants.append(item)
        item = ready(); item["overall_confidence"] = "medium"; variants.append(item)
        item = ready(); item.pop("stageviva_review"); variants.append(item)
        item = ready(); item["dates"]["application_deadline"]["value"] = "2020-01-01"; variants.append(item)
        for index, item in enumerate(variants):
            with self.subTest(index=index):
                url = f"https://www.instagram.com/p/Variant{index}/"
                self.enqueue(request_id=f"variant_{index}", url=url)
                self.ai.return_value = item
                result = self.process()
                self.assertEqual(result["status"], "review")
                self.assertFalse(self.storage.opportunity_by_listing_url(url)["visible"])
        for policy in ("is_school_or_training_opportunity", "is_non_performer_opportunity"):
            self.ai.return_value = ready()
            self.enqueue(request_id=policy, url=f"https://www.instagram.com/p/{policy}/")
            with patch("opportunity_imports." + policy, return_value=True): self.assertEqual(self.process()["status"], "review")

    def test_repeated_source_avoids_paid_analysis_and_preserves_manual_edits(self):
        self.enqueue(); original = self.process()
        self.storage.update_opportunity_title(original["opportunity_id"], "Owner corrected this title")
        self.enqueue(request_id="another_request")
        result = self.process()
        self.assertEqual(result["status"], "duplicate")
        self.ai.assert_called_once()
        self.assertEqual(self.storage.opportunity_by_listing_url(URL)["opportunity"]["identity"]["title"]["value"], "Owner corrected this title")

    def test_cross_source_duplicate_keeps_original_visible_and_saves_hidden_evidence(self):
        self.enqueue(); self.process()
        self.ai.return_value = ready()
        self.enqueue(request_id="cross_source_123", url="https://www.instagram.com/p/SecondPost/")
        result = self.process()
        self.assertEqual(result["status"], "duplicate")
        self.assertTrue(self.storage.opportunity_by_listing_url(URL)["visible"])
        self.assertFalse(self.storage.opportunity_by_listing_url("https://www.instagram.com/p/SecondPost/")["visible"])

    def test_case_sensitive_shortcodes_do_not_collide(self):
        first = self.storage.upsert_opportunity(DiscoveredOpportunity("One", URL, "Test", URL, "manual"), ready())
        other = URL.replace("AbCd", "abcd")
        second = self.storage.upsert_opportunity(DiscoveredOpportunity("Two", other, "Test", other, "manual"), ready())
        self.assertNotEqual(first, second)
        self.assertEqual(self.storage.upsert_opportunity(DiscoveredOpportunity("One", URL, "Test", URL, "manual"), ready()), first)

    def test_budget_pauses_and_requires_explicit_retry(self):
        job = self.enqueue()
        self.ai.side_effect = AIRequestsDisabledError("Limit reached")
        self.assertEqual(self.process()["status"], "paused")
        self.assertIsNone(self.queue.claim())
        self.ai.side_effect = None; self.ai.return_value = ready()
        self.queue.retry(job["id"], "owner", CAPTION, False)
        self.assertEqual(self.process()["status"], "published")

    def test_restart_does_not_repeat_uncertain_ai_but_cached_extraction_can_resume(self):
        job = self.enqueue()
        self.queue.claim()
        self.storage.connection.execute("UPDATE opportunity_imports SET status='analysing',updated_at=? WHERE id=?", (time.time() - 600, job["id"]))
        self.storage.connection.commit()
        self.queue.recover()
        self.assertEqual(self.queue.get(job["id"])["status"], "interrupted")
        self.assertIsNone(self.queue.claim())
        self.ai.assert_not_called()
        item = ready(); item["stageviva_review"]["import_id"] = job["id"]
        self.queue.update(job["id"], status="publishing", opportunity_json=json.dumps(item))
        self.storage.connection.execute("UPDATE opportunity_imports SET updated_at=? WHERE id=?", (time.time() - 600, job["id"]))
        self.storage.connection.commit()
        self.assertEqual(self.process()["status"], "published")
        self.ai.assert_not_called()

    def test_revoked_admin_does_not_analyse_or_publish(self):
        job = self.enqueue()
        self.queue.process(self.queue.claim(), lambda _: False)
        self.assertEqual(self.queue.get(job["id"])["status"], "needs_information")
        self.ai.assert_not_called()

    def test_matching_resume_uses_current_record_and_does_not_reanalyse(self):
        self.enqueue(); result = self.process()
        matcher = Mock(side_effect=AIRequestsDisabledError())
        self.assertTrue(self.queue.match_one(lambda _: True, matcher))
        self.assertEqual(self.queue.get(result["id"])["matching_status"], "paused")
        self.queue.retry(result["id"], "owner", CAPTION, False)
        matcher.side_effect = None; matcher.return_value = {"remaining": False}
        self.storage.update_opportunity_title(result["opportunity_id"], "Updated cruise dancers")
        self.queue.match_one(lambda _: True, matcher)
        self.assertEqual(matcher.call_args.args[1]["identity"]["title"]["value"], "Updated cruise dancers")
        self.assertEqual(self.queue.get(result["id"])["matching_status"], "complete")
        self.ai.assert_called_once()

    def test_claim_serializes_same_source_across_connections(self):
        self.enqueue()
        self.enqueue(request_id="second_request")
        self.assertIsNotNone(self.queue.claim())
        other = StageVivaStorage(str(self.path / "test.db"))
        try: self.assertIsNone(ImportQueue(other, self.path / "private").claim())
        finally: other.close()

    def test_blocked_external_page_needs_supplied_information(self):
        self.fetch.side_effect = PublicSourceError("Blocked")
        self.enqueue(url="https://example.org/auditions", caption="")
        self.assertEqual(self.process()["status"], "needs_information")
        self.ai.assert_not_called()

    def test_link_candidates_are_bounded_and_private_urls_rejected(self):
        links = research_links("https://127.0.0.1/secret https://instagram.com/p/Test/ https://company.org/contact https://company.org/apply https://company.org/auditions https://company.org/four")
        self.assertEqual(links, ["https://company.org/apply", "https://company.org/auditions"])

    def test_external_listing_provenance_and_research_survive_budget_retry(self):
        url = "https://company.org/auditions"
        html = f'<main>{CAPTION}<a href="https://company.org/apply/role">Apply for this role</a></main>'
        self.fetch.side_effect = None
        self.fetch.return_value = (html, url)
        job = self.enqueue(url=url, caption="")
        self.ai.side_effect = AIRequestsDisabledError()
        self.assertEqual(self.process()["status"], "paused")
        self.assertEqual(self.queue.get(job["id"])["source_kind"], "listing")
        self.ai.side_effect = None; self.ai.return_value = ready()
        self.queue.retry(job["id"], "owner", "", False)
        self.assertEqual(self.process()["status"], "published")
        self.assertEqual(self.ai.call_args.kwargs["listing_text_kind"], "listing")
        self.assertEqual(self.ai.call_args.kwargs["research_urls"], ["https://company.org/apply/role"])
        self.fetch.assert_called_once()

    def test_matching_only_spends_one_new_call_per_iteration_and_resumes_from_cache(self):
        storage = Mock()
        storage.list_registered_artists.return_value = [(f"artist-{index}", {"index": index}) for index in range(3)]
        cached = {}
        storage.cached_match.side_effect = lambda artist, oid, signature: cached.get(artist)
        storage.upsert_match.side_effect = lambda artist, oid, result: cached.update({artist: result})
        with patch.object(api, "match_artist_to_opportunity", return_value={"overall": {}}) as matcher, patch.object(api, "match_input_signature", return_value="signature"):
            self.assertTrue(api._match_opportunity_against_registered_artists("opportunity", ready(), storage, max_new_matches=1)["remaining"])
            self.assertEqual(matcher.call_count, 1)
            self.assertTrue(api._match_opportunity_against_registered_artists("opportunity", ready(), storage, max_new_matches=1)["remaining"])
            self.assertFalse(api._match_opportunity_against_registered_artists("opportunity", ready(), storage, max_new_matches=1)["remaining"])
            self.assertFalse(api._match_opportunity_against_registered_artists("opportunity", ready(), storage, max_new_matches=1)["remaining"])
            self.assertEqual(matcher.call_count, 3)


class ImportApiTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patchers = [patch.object(api, "DATABASE_PATH", str(Path(self.directory.name) / "api.db")),
                         patch.object(api, "IMPORTS_DIR", Path(self.directory.name) / "private"),
                         patch.object(api, "_lovable_auth_enabled", return_value=False),
                         patch.object(api, "_start_import_worker"),
                         patch.dict(api.os.environ, {"STAGEVIVA_ADMIN_EMAILS": "owner@example.com,other@example.com"})]
        for item in self.patchers: item.start()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patchers): item.stop()
        self.directory.cleanup()

    def headers(self, email):
        response = self.client.post("/auth/register", json={"email": email, "password": "safe-password-123", "display_name": "Test"})
        self.assertEqual(response.status_code, 201, response.text)
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    def test_admin_only_owner_scoped_durable_multipart_and_readonly_polling(self):
        normal, owner, other = [self.headers(email) for email in ("performer@example.com", "owner@example.com", "other@example.com")]
        data = {"source_url": URL, "request_id": "api_request_123", "caption": CAPTION}
        self.assertEqual(self.client.get("/admin/opportunity-imports").status_code, 401)
        self.assertEqual(self.client.get("/admin/opportunity-imports", headers=normal).status_code, 403)
        self.assertEqual(self.client.post("/admin/opportunity-imports", data=data, headers=normal).status_code, 403)
        response = self.client.post("/admin/opportunity-imports", data=data, files=[("screenshots", ("../../poster.png", screenshot(), "image/png"))], headers=owner)
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        with patch("opportunity_imports.analyse_opportunity") as analyser:
            self.assertEqual(self.client.get("/admin/opportunity-imports", headers=owner).json()[0]["id"], job["id"])
            self.assertEqual(self.client.get("/admin/opportunity-imports", headers=other).json(), [])
            self.assertEqual(self.client.post(f"/admin/opportunity-imports/{job['id']}/retry", headers=other, json={}).status_code, 404)
            image_path = f"/admin/opportunity-imports/{job['id']}/screenshots/0"
            image_response = self.client.get(image_path, headers=owner)
            self.assertEqual(image_response.status_code, 200)
            self.assertEqual(image_response.content, screenshot())
            self.assertEqual(image_response.headers["cache-control"], "private, no-store")
            self.assertEqual(self.client.get(image_path, headers=other).status_code, 404)
            self.assertEqual(self.client.get(image_path, headers=normal).status_code, 403)
            self.assertEqual(self.client.get(image_path[:-1] + "-1", headers=owner).status_code, 404)
            analyser.assert_not_called()
        self.assertEqual(self.client.post("/admin/opportunity-imports", data={**data, "source_url": "http://127.0.0.1/job"}, headers=owner).status_code, 422)


if __name__ == "__main__": unittest.main()
