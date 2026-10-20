from __future__ import annotations

import tempfile
import unittest
import hashlib
import hmac
import json
import time
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import api


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        api.DATABASE_PATH = str(Path(self.directory.name) / "stageviva.db")
        api.UPLOADS_DIR = Path(self.directory.name) / "uploads"
        self.local_auth = patch.object(api, "_lovable_auth_enabled", return_value=False)
        self.local_auth.start()
        self.client = TestClient(api.app)

    def tearDown(self) -> None:
        self.local_auth.stop()
        self.directory.cleanup()

    def register(self, email: str, name: str) -> str:
        response = self.client.post("/auth/register", json={
            "email": email, "password": "safe-password-123", "display_name": name,
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["access_token"]

    def seed_tracker_opportunity(self, user_id: str, opportunity_id: str = "tracker-opportunity") -> str:
        deadline = date.today() + timedelta(days=3)
        opportunity = {
            "identity": {
                "title": {"value": "StageViva tracker audition"},
                "organisation": {"value": "Example Dance Company"},
                "opportunity_type": {"value": "Paid company contract"},
            },
            "location": {"city": {"value": "London"}, "country": {"value": "United Kingdom"}},
            "dates": {"application_deadline": {"value": deadline.isoformat()}},
        }
        storage = api.StageVivaStorage(api.DATABASE_PATH)
        try:
            storage.connection.execute("""
                INSERT INTO opportunities (
                    id, listing_url, title, source_name, source_url, category,
                    opportunity_json, visible, created_at, updated_at
                ) VALUES (?, ?, ?, 'Trusted Source', 'https://example.test', 'dance', ?, 1, 'now', 'now')
            """, (opportunity_id, f"https://example.test/{opportunity_id}",
                  "StageViva tracker audition", json.dumps(opportunity)))
            storage.connection.commit()
            user = storage.get_user(user_id)
            artist_id = user.get("artist_id") or storage.upsert_artist_for_user(
                user_id, {"identity": {"name": "Tracker Artist"}},
            )
            storage.upsert_match(artist_id, opportunity_id, {
                "overall": {"match_score": 80, "match_level": "high"},
            })
        finally:
            storage.close()
        return opportunity_id

    def test_png_cv_upload_is_accepted(self) -> None:
        token = self.register("image-cv@example.com", "Image CV")
        headers = {"Authorization": f"Bearer {token}"}
        png = b"\x89PNG\r\n\x1a\n" + b"image-data"
        with patch.object(api, "_analyse_uploaded_cv_in_background"):
            response = self.client.post(
                "/me/cv",
                headers=headers,
                files={"cv": ("cv.png", png, "image/png")},
            )
        self.assertEqual(response.status_code, 202)

    def test_fake_jpeg_cv_is_rejected_without_using_allowance(self) -> None:
        token = self.register("fake-image-cv@example.com", "Fake Image CV")
        headers = {"Authorization": f"Bearer {token}"}
        response = self.client.post(
            "/me/cv",
            headers=headers,
            files={"cv": ("cv.jpg", b"not-a-jpeg", "image/jpeg")},
        )
        self.assertEqual(response.status_code, 400)
        access = self.client.get("/me/access", headers=headers).json()
        self.assertEqual(access["cv_uploads_used_this_month"], 0)

    def test_user_can_manage_their_private_performer_profile(self) -> None:
        token = self.register("artist@example.com", "Ava Artist")
        headers = {"Authorization": f"Bearer {token}"}

        profile = self.client.put("/me/profile", headers=headers, json={
            "display_name": "Ava Artist", "profile": {"performer_types": ["dance", "singing"]},
        })
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json()["profile"]["performer_types"], ["dance", "singing"])

        saved = self.client.put("/me/artist-dna", headers=headers, json={
            "artist_dna": {"identity": {"name": "Ava Artist"}, "disciplines": []},
        })
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["matched_opportunities"], 0)

        dna = self.client.get("/me/artist-dna", headers=headers)
        self.assertEqual(dna.status_code, 200)
        self.assertEqual(dna.json()["dna"]["identity"]["name"], "Ava Artist")

        other_token = self.register("other@example.com", "Other Artist")
        self.assertEqual(self.client.get("/matches", headers={"Authorization": f"Bearer {other_token}"}).json(), [])

    def test_failed_cv_analysis_returns_the_upload_allowance(self) -> None:
        token = self.register("unreadable@example.com", "Unreadable Artist")
        headers = {"Authorization": f"Bearer {token}"}
        with patch.object(api, "analyse_artist", side_effect=RuntimeError(
            "No usable text could be extracted from uploaded CV."
        )):
            response = self.client.post(
                "/me/cv", headers=headers,
                files={"cv": ("scan.pdf", b"not-empty", "application/pdf")},
            )
        self.assertEqual(response.status_code, 202, response.text)
        analysis = self.client.get("/me/cv-analysis", headers=headers)
        self.assertEqual(analysis.json()["status"], "failed")
        self.assertEqual(analysis.json()["cv_uploads"]["used"], 0)
        self.assertIn("couldn't read any text", analysis.json()["error"])

    def test_legacy_unreadable_cv_is_claimed_for_one_ocr_retry(self) -> None:
        storage = api.StageVivaStorage(api.DATABASE_PATH)
        try:
            user = storage.create_user("legacy-scan@example.com", "Legacy Scan", "hash")
            storage.start_cv_analysis(user["id"])
            storage.complete_cv_analysis(
                user["id"], "No usable text could be extracted from /uploads/cv.pdf.",
            )
            self.assertEqual(storage.claim_legacy_unreadable_cv_retries(), [user["id"]])
            self.assertEqual(storage.get_cv_analysis(user["id"])["status"], "processing")
            self.assertEqual(storage.claim_legacy_unreadable_cv_retries(), [])
        finally:
            storage.close()

    def test_user_can_register_a_private_phone_push_subscription(self) -> None:
        token = self.register("push@example.com", "Push Artist")
        headers = {"Authorization": f"Bearer {token}"}
        public_key = self.client.get("/me/push/public-key", headers=headers)
        self.assertEqual(public_key.status_code, 200, public_key.text)
        self.assertGreater(len(public_key.json()["public_key"]), 80)
        subscription = {
            "endpoint": "https://push.example.test/subscription/123",
            "keys": {"p256dh": "browser-public-key", "auth": "browser-auth-key"},
        }
        self.assertEqual(self.client.post("/me/push-subscriptions/test", headers=headers).json(), {"sent": 0})
        saved = self.client.post("/me/push-subscriptions", headers=headers, json=subscription)
        self.assertEqual(saved.status_code, 204, saved.text)
        removed = self.client.request("DELETE", "/me/push-subscriptions", headers=headers, json=subscription)
        self.assertEqual(removed.status_code, 204, removed.text)

    def test_premium_feed_keeps_current_low_or_ineligible_matches_visible(self) -> None:
        token = self.register("catalogue@example.com", "Catalogue Artist")
        headers = {"Authorization": f"Bearer {token}"}
        self.client.put("/me/artist-dna", headers=headers, json={
            "artist_dna": {"identity": {"name": "Catalogue Artist"}},
        })
        storage = api.StageVivaStorage(api.DATABASE_PATH)
        try:
            user = storage.get_user_by_email("catalogue@example.com")
            self.assertIsNotNone(user)
            storage.update_membership_tier(user["id"], "pro")
            opportunity = {
                "identity": {
                    "title": {"value": "Current trusted audition"},
                    "organisation": {"value": "Trusted Company"},
                    "opportunity_type": {"value": "Company contract"},
                    "description": {"value": "A current professional opportunity."},
                },
                "location": {"city": {"value": "Paris"}, "country": {"value": "France"}},
                "dates": {"application_deadline": {"value": "31 December 2099"}},
            }
            storage.connection.execute("""
                INSERT INTO opportunities (
                    id, listing_url, title, source_name, source_url, category,
                    opportunity_json, visible, created_at, updated_at
                ) VALUES ('current-low', 'https://trusted.example/current', 'Current trusted audition',
                    'Trusted Source', 'https://trusted.example', 'dance', ?, 1, 'now', 'now')
            """, (__import__("json").dumps(opportunity),))
            storage.upsert_match(user["artist_id"], "current-low", {
                "overall": {"match_score": 12, "match_level": "low", "recommendation": "not_recommended"},
                "eligibility": {"status": "ineligible"},
            })
        finally:
            storage.close()

        matches = self.client.get("/matches", headers=headers)
        self.assertEqual(matches.status_code, 200, matches.text)
        self.assertEqual([item["opportunity_id"] for item in matches.json()], ["current-low"])

    def test_owner_can_moderate_and_add_manual_opportunities(self) -> None:
        token = self.register("owner@example.com", "StageViva Owner")
        headers = {"Authorization": f"Bearer {token}"}
        with patch.dict(api.os.environ, {"STAGEVIVA_ADMIN_EMAILS": "owner@example.com"}):
            self.assertTrue(self.client.get("/me", headers=headers).json()["is_admin"])
            operations = self.client.get("/admin/operations", headers=headers)
            self.assertEqual(operations.status_code, 200, operations.text)
            self.assertIn("sources", operations.json())
            created = self.client.post("/admin/opportunities", headers=headers, json={
                "title": "Example Ballet — company dancer contract",
                "organisation": "Example Ballet",
                "role_summary": "Company dancer audition",
                "location": "London, United Kingdom",
                "deadline": "31 December 2099",
                "official_url": "https://example.org/audition",
                "contract_type": "Paid company contract",
                "description": "Paid professional employment for dancers.",
            })
            self.assertEqual(created.status_code, 201, created.text)
            opportunity_id = created.json()["id"]
            listings = self.client.get("/admin/opportunities", headers=headers)
            self.assertEqual(listings.status_code, 200, listings.text)
            self.assertEqual(listings.json()[0]["title"], "Example Ballet — company dancer contract")
            updated = self.client.patch(f"/admin/opportunities/{opportunity_id}", headers=headers, json={
                "title": "Example Ballet — paid company contract", "visible": False,
            })
            self.assertEqual(updated.status_code, 200, updated.text)
            listing = self.client.get("/admin/opportunities", headers=headers).json()[0]
            self.assertEqual(listing["title"], "Example Ballet — paid company contract")
            self.assertFalse(listing["visible"])
            details = self.client.patch(f"/admin/opportunities/{opportunity_id}", headers=headers, json={
                "description": "Updated paid employment description.",
                "official_url": "https://example.org/updated-audition",
                "deadline": "1 January 2100",
                "country": "United Kingdom",
                "track": "apprenticeship",
                "categories": ["ballet", "contemporary"],
            })
            self.assertEqual(details.status_code, 200, details.text)
            listing = self.client.get("/admin/opportunities", headers=headers).json()[0]
            self.assertEqual(listing["description"], "Updated paid employment description.")
            self.assertEqual(listing["official_url"], "https://example.org/updated-audition")
            self.assertEqual(listing["country"], "United Kingdom")
            self.assertEqual(listing["track"], "apprenticeship")
            self.assertEqual(listing["categories"], ["ballet", "contemporary"])

            school = self.client.post("/admin/opportunities", headers=headers, json={
                "title": "Example Ballet School Summer Intensive",
                "organisation": "Example Ballet School",
                "role_summary": "Student admission",
                "location": "London",
                "deadline": "31 December 2099",
                "official_url": "https://school.example/summer-course",
                "contract_type": "Training course",
                "description": "Apply for classes, tuition and a dance diploma.",
            })
            self.assertEqual(school.status_code, 422, school.text)

    def test_lovable_session_creates_and_reuses_one_stageviva_user(self) -> None:
        claims = {
            "sub": "lovable-user-123", "email": "lovable@example.com",
            "user_metadata": {"full_name": "Lovable Performer"},
        }
        headers = {"Authorization": "Bearer lovable-session-token"}
        with patch.object(api, "_lovable_auth_enabled", return_value=True), patch.object(
            api, "_verify_lovable_token", return_value=claims,
        ):
            first = self.client.get("/me", headers=headers)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["email"], "lovable@example.com")
            self.assertEqual(first.json()["display_name"], "Lovable Performer")

            self.client.put("/me/profile", headers=headers, json={
                "display_name": "Lovable Performer",
                "profile": {"performer_types": ["dance"]},
            })
            second = self.client.get("/me", headers=headers)
            self.assertEqual(second.status_code, 200)
            self.assertEqual(second.json()["id"], first.json()["id"])
            self.assertEqual(second.json()["profile"]["performer_types"], ["dance"])

    def test_known_profile_answers_save_without_an_ai_round_trip(self) -> None:
        token = self.register("answers@example.com", "Answer Artist")
        headers = {"Authorization": f"Bearer {token}"}
        self.client.put("/me/artist-dna", headers=headers, json={
            "artist_dna": {"identity": {"name": "Answer Artist"}, "intelligence": {}},
        })
        enriched = {
            "identity": {"name": "Answer Artist", "nationality": "Spanish"},
            "intelligence": {"missing_information": [], "questions_to_ask": []},
        }
        with patch.object(api, "enrich_artist_dna", return_value=enriched) as enrich:
            response = self.client.post("/me/artist-dna/answers", headers=headers, json={
                "answers": [{"question": "What is your nationality?", "answer": "Spanish"}],
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["artist_dna"]["identity"]["nationality"], "Spanish")
        enrich.assert_not_called()

    def test_profile_answer_never_starts_a_catalogue_ai_rematch(self) -> None:
        token = self.register("safe-answers@example.com", "Safe Artist")
        headers = {"Authorization": f"Bearer {token}"}
        self.client.put("/me/artist-dna", headers=headers, json={
            "artist_dna": {
                "identity": {"name": "Safe Artist"},
                "preferences": {"availability": "unknown"},
            },
        })
        with patch.object(api, "match_artist_to_opportunity") as match:
            response = self.client.post("/me/artist-dna/answers", headers=headers, json={
                "answers": [{"id": "availability", "answer": "Within 3 months"}],
            })
        self.assertEqual(response.status_code, 200, response.text)
        match.assert_not_called()

    def test_contract_preference_answer_clears_the_matching_question(self) -> None:
        token = self.register("contracts@example.com", "Contract Artist")
        headers = {"Authorization": f"Bearer {token}"}
        self.client.put("/me/artist-dna", headers=headers, json={
            "artist_dna": {
                "identity": {"name": "Contract Artist", "age": "20", "nationality": "Spanish", "location": "Cardiff"},
                "eligibility": {"work_rights": ["UK"], "visa_status": "unknown"},
                "preferences": {"availability": "Available now", "relocation_preferences": "Yes", "contract_preferences": []},
            },
        })
        response = self.client.post("/me/artist-dna/answers", headers=headers, json={
            "answers": [{"id": "contract_preferences", "answer": "Paid company contract, Cruise/resort contract"}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["artist_dna"]["preferences"]["contract_preferences"], [
            "Paid company contract", "Cruise/resort contract",
        ])
        questions = self.client.get("/me/profile-questions", headers=headers).json()["questions"]
        self.assertNotIn("contract_preferences", [question["id"] for question in questions])

    def test_work_rights_answer_clears_the_matching_question(self) -> None:
        token = self.register("rights@example.com", "Rights Artist")
        headers = {"Authorization": f"Bearer {token}"}
        self.client.put("/me/artist-dna", headers=headers, json={
            "artist_dna": {
                "identity": {"name": "Rights Artist"},
                "eligibility": {"work_rights": [], "visa_status": "unknown"},
            },
        })
        response = self.client.post("/me/artist-dna/answers", headers=headers, json={
            "answers": [{"id": "work_rights", "answer": "Yes"}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["artist_dna"]["eligibility"]["visa_status"], "Yes")
        questions = self.client.get("/me/profile-questions", headers=headers).json()["questions"]
        self.assertNotIn("work_rights", [question["id"] for question in questions])

    def test_gender_answer_is_saved_in_artist_dna(self) -> None:
        token = self.register("gender@example.com", "Gender Artist")
        headers = {"Authorization": f"Bearer {token}"}
        self.client.put("/me/artist-dna", headers=headers, json={
            "artist_dna": {"identity": {"name": "Gender Artist", "gender": "unknown"}},
        })
        response = self.client.post("/me/artist-dna/answers", headers=headers, json={
            "answers": [{"id": "gender", "answer": "Female"}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["artist_dna"]["identity"]["gender"], "female")

    def test_private_headshot_is_available_after_cv_analysis(self) -> None:
        token = self.register("headshot@example.com", "Headshot Artist")
        headers = {"Authorization": f"Bearer {token}"}
        cv_file = ("artist.pdf", b"pretend-pdf", "application/pdf")
        with patch.object(api, "analyse_artist", return_value={"identity": {"name": "Headshot Artist"}}), patch.object(
            api, "extract_cv_headshot",
        ) as extract:
            def save_headshot(_cv_path: str, destination: Path) -> bool:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"jpeg-data")
                return True

            extract.side_effect = save_headshot
            response = self.client.post("/me/cv", headers=headers, files={"cv": cv_file})
        self.assertEqual(response.status_code, 202, response.text)
        headshot = self.client.get("/me/headshot", headers=headers)
        self.assertEqual(headshot.status_code, 200, headshot.text)
        self.assertEqual(headshot.headers["content-type"], "image/jpeg")
        dna = self.client.get("/me/artist-dna", headers=headers)
        self.assertTrue(dna.json()["dna"]["physical"]["headshot_available"])

    def test_matching_profile_questions_are_short_and_structured(self) -> None:
        token = self.register("questions@example.com", "Question Artist")
        headers = {"Authorization": f"Bearer {token}"}
        self.client.put("/me/artist-dna", headers=headers, json={
            "artist_dna": {
                "identity": {"name": "Question Artist", "age": "unknown", "nationality": "unknown", "location": "unknown"},
                "eligibility": {"work_rights": [], "visa_status": "unknown"},
                "preferences": {"availability": "unknown", "relocation_preferences": "unknown", "contract_preferences": []},
            },
        })
        response = self.client.get("/me/profile-questions", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        questions = response.json()["questions"]
        self.assertIn("availability", [question["id"] for question in questions])
        relocation = next(question for question in questions if question["id"] == "relocation")
        self.assertEqual(relocation["type"], "single_select")

    def test_creator_can_grant_complimentary_premium(self) -> None:
        owner_token = self.register("owner@example.com", "StageViva Owner")
        performer_token = self.register("premium@example.com", "Premium Artist")
        performer_headers = {"Authorization": f"Bearer {performer_token}"}
        performer = self.client.get("/me", headers=performer_headers).json()
        self.assertEqual(performer["membership_tier"], "free")
        with patch.dict(api.os.environ, {"STAGEVIVA_ADMIN_EMAILS": "owner@example.com"}):
            owner_headers = {"Authorization": f"Bearer {owner_token}"}
            overview = self.client.get("/admin/performers", headers=owner_headers)
            self.assertEqual(overview.status_code, 200, overview.text)
            self.assertIn(performer["email"], [item["email"] for item in overview.json()])
            granted = self.client.patch(
                f"/admin/performers/{performer['id']}/membership", headers=owner_headers,
                json={"membership_tier": "pro"},
            )
        self.assertEqual(granted.status_code, 200, granted.text)
        self.assertEqual(granted.json()["membership_tier"], "pro")
        access = self.client.get("/me/access", headers=performer_headers)
        self.assertEqual(access.json()["opportunities"]["release"], "immediate")
        self.assertTrue(access.json()["opportunities"]["show_full_details"])

    def test_stripe_checkout_uses_the_authenticated_user_and_selected_test_price(self) -> None:
        token = self.register("buyer@example.com", "Premium Buyer")
        headers = {"Authorization": f"Bearer {token}"}
        configured = {
            "STAGEVIVA_STRIPE_MODE": "test",
            "STAGEVIVA_STRIPE_REQUESTS_ENABLED": "true",
            "STRIPE_PREMIUM_MONTHLY_PRICE_ID": "price_test_monthly",
            "STRIPE_AUDITION_SEASON_PRICE_ID": "price_test_season",
        }
        with patch.dict(api.os.environ, configured, clear=False), patch.object(
            api, "create_checkout_session",
            return_value={"id": "cs_test_safe", "url": "https://checkout.stripe.com/c/pay/test"},
        ) as create_session:
            response = self.client.post(
                "/billing/checkout", headers=headers, json={"plan": "premium_monthly"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["session_id"], "cs_test_safe")
        call = create_session.call_args.kwargs
        self.assertEqual(call["email"], "buyer@example.com")
        self.assertEqual(call["plan"], "premium_monthly")
        self.assertTrue(call["user_id"].startswith("user_"))

    def test_signed_stripe_webhook_grants_premium_once_and_cancellation_removes_it(self) -> None:
        token = self.register("stripe-member@example.com", "Stripe Member")
        headers = {"Authorization": f"Bearer {token}"}
        user_id = self.client.get("/me", headers=headers).json()["id"]
        configured = {
            "STRIPE_WEBHOOK_SECRET": "whsec_test_stageviva",
            "STRIPE_PREMIUM_MONTHLY_PRICE_ID": "price_test_monthly",
            "STRIPE_AUDITION_SEASON_PRICE_ID": "price_test_season",
        }

        def deliver(event: dict) -> object:
            payload = json.dumps(event, separators=(",", ":")).encode()
            timestamp = int(time.time())
            signature = hmac.new(
                configured["STRIPE_WEBHOOK_SECRET"].encode(),
                f"{timestamp}.".encode() + payload,
                hashlib.sha256,
            ).hexdigest()
            return self.client.post(
                "/billing/webhook", content=payload,
                headers={"Stripe-Signature": f"t={timestamp},v1={signature}", "Content-Type": "application/json"},
            )

        active_subscription = {
            "id": "sub_test_stageviva", "customer": "cus_test_stageviva", "status": "active",
            "cancel_at_period_end": False, "current_period_end": 1_900_000_000,
            "metadata": {"stageviva_user_id": user_id, "stageviva_plan": "premium_monthly"},
            "items": {"data": [{"price": {"id": "price_test_monthly"}}]},
        }
        active_event = {
            "id": "evt_test_active", "type": "customer.subscription.updated",
            "data": {"object": active_subscription},
        }
        with patch.dict(api.os.environ, configured, clear=False):
            granted = deliver(active_event)
            duplicate = deliver(active_event)
        self.assertEqual(granted.status_code, 200, granted.text)
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(self.client.get("/me", headers=headers).json()["membership_tier"], "pro")

        cancelled_subscription = {**active_subscription, "status": "canceled"}
        cancelled_event = {
            "id": "evt_test_cancelled", "type": "customer.subscription.deleted",
            "data": {"object": cancelled_subscription},
        }
        with patch.dict(api.os.environ, configured, clear=False):
            cancelled = deliver(cancelled_event)
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(self.client.get("/me", headers=headers).json()["membership_tier"], "free")

    def test_stripe_cancellation_does_not_remove_a_manual_founder_grant(self) -> None:
        token = self.register("complimentary@example.com", "Complimentary Artist")
        headers = {"Authorization": f"Bearer {token}"}
        user_id = self.client.get("/me", headers=headers).json()["id"]
        storage = api.StageVivaStorage(api.DATABASE_PATH)
        try:
            storage.update_membership_tier(user_id, "pro")
            storage.upsert_billing_subscription(
                user_id,
                provider_customer_id="cus_old",
                provider_subscription_id="sub_old",
                plan="premium_monthly",
                price_id="price_test_monthly",
                status="canceled",
                current_period_end=1_900_000_000,
                cancel_at_period_end=False,
            )
            storage.update_stripe_membership(user_id, entitled=False)
        finally:
            storage.close()
        self.assertEqual(self.client.get("/me", headers=headers).json()["membership_tier"], "pro")

    def test_cv_insights_are_a_premium_benefit(self) -> None:
        token = self.register("insights@example.com", "Insight Artist")
        headers = {"Authorization": f"Bearer {token}"}
        self.client.put("/me/artist-dna", headers=headers, json={
            "artist_dna": {"identity": {"name": "Insight Artist"}, "physical": {"headshot_available": False}},
        })
        self.assertEqual(self.client.get("/me/cv-insights", headers=headers).status_code, 403)
        storage = api.StageVivaStorage(api.DATABASE_PATH)
        try:
            storage.update_membership_tier(self.client.get("/me", headers=headers).json()["id"], "pro")
        finally:
            storage.close()
        response = self.client.get("/me/cv-insights", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Headshot", [item["area"] for item in response.json()["items"]])

    def test_free_access_is_ready_for_the_weekly_release_model(self) -> None:
        token = self.register("free@example.com", "Free Artist")
        headers = {"Authorization": f"Bearer {token}"}
        storage = api.StageVivaStorage(api.DATABASE_PATH)
        try:
            storage.update_membership_tier(
                self.client.get("/me", headers=headers).json()["id"], "free",
            )
        finally:
            storage.close()
        access = self.client.get("/me/access", headers=headers)
        self.assertEqual(access.status_code, 200, access.text)
        self.assertEqual(access.json()["opportunities"]["release"], "weekly_monday")
        self.assertFalse(access.json()["opportunities"]["show_company_or_role_before_release"])

    def test_basic_user_can_save_and_track_but_premium_tools_are_locked(self) -> None:
        token = self.register("tracker-basic@example.com", "Basic Tracker")
        headers = {"Authorization": f"Bearer {token}"}
        user_id = self.client.get("/me", headers=headers).json()["id"]
        opportunity_id = self.seed_tracker_opportunity(user_id)

        saved = self.client.put(
            f"/me/opportunity-tracker/{opportunity_id}", headers=headers,
            json={"application_status": "saved"},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["premium_features"]["reminders_locked"])
        self.assertTrue(saved.json()["premium_features"]["calendar_locked"])

        updated = self.client.put(
            f"/me/opportunity-tracker/{opportunity_id}", headers=headers,
            json={"application_status": "preparing", "notes": "Update my showreel."},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["application_status"], "preparing")
        self.assertEqual(updated.json()["notes"], "Update my showreel.")

        locked = self.client.put(
            f"/me/opportunity-tracker/{opportunity_id}", headers=headers,
            json={"reminder_days": [3, 2, 0]},
        )
        self.assertEqual(locked.status_code, 403, locked.text)
        self.assertEqual(locked.json()["detail"]["code"], "premium_required")
        self.assertEqual(self.client.get(
            f"/me/opportunity-tracker/{opportunity_id}/calendar.ics", headers=headers,
        ).status_code, 403)

    def test_premium_user_gets_one_deadline_reminder_and_calendar(self) -> None:
        token = self.register("tracker-premium@example.com", "Premium Tracker")
        headers = {"Authorization": f"Bearer {token}"}
        user_id = self.client.get("/me", headers=headers).json()["id"]
        opportunity_id = self.seed_tracker_opportunity(user_id)
        storage = api.StageVivaStorage(api.DATABASE_PATH)
        try:
            storage.update_membership_tier(user_id, "pro")
        finally:
            storage.close()

        saved = self.client.put(
            f"/me/opportunity-tracker/{opportunity_id}", headers=headers,
            json={
                "application_status": "saved", "reminder_days": [3, 2, 0],
                "audition_at": "2099-01-10T10:30:00", "audition_location": "Studio 1",
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertFalse(saved.json()["premium_features"]["reminders_locked"])
        storage = api.StageVivaStorage(api.DATABASE_PATH)
        try:
            self.assertEqual(storage.materialize_due_deadline_reminders(date.today()), 1)
            self.assertEqual(storage.materialize_due_deadline_reminders(date.today()), 0)
            reminders = storage.list_deadline_reminders_for_user(user_id)
            self.assertEqual(len(reminders), 1)
            self.assertEqual(reminders[0]["days_before"], 3)
        finally:
            storage.close()

        calendar = self.client.get(
            f"/me/opportunity-tracker/{opportunity_id}/calendar.ics", headers=headers,
        )
        self.assertEqual(calendar.status_code, 200, calendar.text)
        self.assertIn("BEGIN:VCALENDAR", calendar.text)
        self.assertIn("Studio 1", calendar.text)

        applied = self.client.put(
            f"/me/opportunity-tracker/{opportunity_id}", headers=headers,
            json={"application_status": "applied"},
        )
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()["reminder_days"], [])
        storage = api.StageVivaStorage(api.DATABASE_PATH)
        try:
            self.assertEqual(storage.list_deadline_reminders_for_user(user_id), [])
        finally:
            storage.close()


    def test_existing_daily_cron_runs_reminders_without_enabling_ai_scans(self) -> None:
        configured = {
            "STAGEVIVA_SCHEDULER_SECRET": "test-scheduler-secret",
            "STAGEVIVA_SOURCE_SCANS_ENABLED": "false",
        }
        with (
            patch.dict(api.os.environ, configured, clear=False),
            patch.object(api, "deliver_pending_push_notifications", return_value={"sent": 0}) as web,
            patch.object(api, "deliver_pending_native_push_notifications", return_value={"sent": 0}) as native,
            patch.object(api.threading, "Thread") as thread,
        ):
            response = self.client.post(
                "/internal/daily-source-scan",
                headers={"X-StageViva-Scheduler-Secret": "test-scheduler-secret"},
            )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["status"], "source_scan_disabled")
        self.assertEqual(response.json()["reminders"]["created"], 0)
        web.assert_called_once()
        native.assert_called_once()
        thread.assert_not_called()

    def test_basic_cannot_save_an_unreleased_match_or_another_users_match(self) -> None:
        token = self.register("locked-tracker@example.com", "Locked Tracker")
        headers = {"Authorization": f"Bearer {token}"}
        user_id = self.client.get("/me", headers=headers).json()["id"]
        ids = [self.seed_tracker_opportunity(user_id, f"tracker-{index}") for index in range(4)]
        storage = api.StageVivaStorage(api.DATABASE_PATH)
        try:
            released = storage.weekly_released_match_ids(user_id, api._weekly_release_start(), ids)
        finally:
            storage.close()
        locked_id = next(value for value in ids if value not in released)
        response = self.client.put(
            f"/me/opportunity-tracker/{locked_id}", headers=headers, json={},
        )
        self.assertEqual(response.status_code, 403, response.text)
        other_token = self.register("other-tracker@example.com", "Other Tracker")
        response = self.client.put(
            f"/me/opportunity-tracker/{ids[0]}",
            headers={"Authorization": f"Bearer {other_token}"}, json={},
        )
        self.assertEqual(response.status_code, 404, response.text)


if __name__ == "__main__":
    unittest.main()
