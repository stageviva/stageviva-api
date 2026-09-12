from __future__ import annotations

import tempfile
import unittest
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
            })
            self.assertEqual(details.status_code, 200, details.text)
            listing = self.client.get("/admin/opportunities", headers=headers).json()[0]
            opportunity = listing["opportunity"]
            self.assertEqual(opportunity["identity"]["description"]["value"], "Updated paid employment description.")
            self.assertEqual(opportunity["application"]["application_url"]["value"], "https://example.org/updated-audition")

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

    def test_creator_can_grant_complimentary_beta_premium(self) -> None:
        owner_token = self.register("owner@example.com", "StageViva Owner")
        performer_token = self.register("beta@example.com", "Beta Artist")
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
                json={"membership_tier": "beta"},
            )
        self.assertEqual(granted.status_code, 200, granted.text)
        self.assertEqual(granted.json()["membership_tier"], "beta")
        access = self.client.get("/me/access", headers=performer_headers)
        self.assertEqual(access.json()["opportunities"]["release"], "immediate")
        self.assertTrue(access.json()["opportunities"]["show_full_details"])

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


if __name__ == "__main__":
    unittest.main()
