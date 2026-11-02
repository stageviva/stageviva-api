"""Synthetic CV claims and HTTP uploads; no analysis or app startup jobs."""

import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import api
from storage import StageVivaStorage


class CvUploadSafetyTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.database = root / "synthetic.db"
        self.uploads = root / "uploads"
        for name, value in [("DATABASE_PATH", str(self.database)), ("UPLOADS_DIR", self.uploads)]:
            context = patch.object(api, name, value)
            context.start()
            self.addCleanup(context.stop)
        context = patch.object(api, "_lovable_auth_enabled", return_value=False)
        context.start()
        self.addCleanup(context.stop)
        context = patch.dict(api.os.environ, {"STAGEVIVA_DEFAULT_MEMBERSHIP_TIER": "free"})
        context.start()
        self.addCleanup(context.stop)
        # No context-manager entry: deliberately never invoke app lifespan.
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)
        response = self.client.post("/auth/register", json={
            "email": "synthetic-cv@example.com", "password": "synthetic-password-123", "display_name": "Synthetic artist",
        })
        self.assertEqual(response.status_code, 201)
        self.headers = {"Authorization": "Bearer " + response.json()["access_token"]}
        self.user_id = self.client.get("/me", headers=self.headers).json()["id"]

    def upload(self, content=b"Synthetic CV"):
        return self.client.post("/me/cv", headers=self.headers,
                                files={"cv": ("cv.txt", content, "text/plain")})

    def test_repeated_http_upload_returns_existing_job_without_replacing_cv(self):
        with patch.object(api, "_analyse_uploaded_cv_in_background") as analyse:
            first = self.upload(b"Original synthetic CV")
            second = self.upload(b"Replacement must not overwrite active CV")
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(second.status_code, 202, second.text)
        self.assertEqual(second.json()["status"], "processing")
        self.assertEqual(second.json()["started_at"], first.json()["started_at"])
        self.assertEqual(second.json()["uploads_used"], 1)
        self.assertEqual(analyse.call_count, 1)
        self.assertEqual((self.uploads / self.user_id / "cv.txt").read_bytes(), b"Original synthetic CV")

    def test_file_failure_returns_retryable_error_without_consuming_allowance(self):
        with patch.object(api.tempfile, "NamedTemporaryFile", side_effect=OSError("synthetic disk failure")), \
             patch.object(api, "_analyse_uploaded_cv_in_background") as analyse:
            response = self.upload()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("allowance hasn't been used", response.json()["detail"])
        analyse.assert_not_called()
        storage = StageVivaStorage(self.database)
        try:
            self.assertEqual(storage.cv_upload_count_this_month(self.user_id), 0)
            self.assertIsNone(storage.get_cv_analysis(self.user_id))
        finally:
            storage.close()

    def test_concurrent_database_connections_claim_only_one_job(self):
        connections = [StageVivaStorage(self.database) for _ in range(8)]
        saved = Mock()
        try:
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(
                    lambda storage: storage.claim_cv_upload_this_month(self.user_id, 2, saved), connections,
                ))
            self.assertEqual([result[0] for result in results].count("started"), 1)
            self.assertEqual([result[0] for result in results].count("processing"), 7)
            saved.assert_called_once()
            self.assertEqual(connections[0].cv_upload_count_this_month(self.user_id), 1)
        finally:
            for storage in connections:
                storage.close()

    def test_completed_job_allows_replacement_but_preserves_monthly_limit(self):
        storage = StageVivaStorage(self.database)
        try:
            saved = Mock()
            self.assertEqual(storage.claim_cv_upload_this_month(self.user_id, 2, saved)[0], "started")
            storage.complete_cv_analysis(self.user_id)
            self.assertEqual(storage.claim_cv_upload_this_month(self.user_id, 2, saved)[0], "started")
            storage.complete_cv_analysis(self.user_id)
            self.assertEqual(storage.claim_cv_upload_this_month(self.user_id, 2, saved)[0], "limit")
            self.assertEqual(saved.call_count, 2)
        finally:
            storage.close()

    def test_active_job_is_returned_even_when_allowance_is_exhausted(self):
        storage = StageVivaStorage(self.database)
        try:
            storage.claim_cv_upload_this_month(self.user_id, 1, Mock())
            saved = Mock()
            outcome, used, job = storage.claim_cv_upload_this_month(self.user_id, 1, saved)
            self.assertEqual((outcome, used, job["status"]), ("processing", 1, "processing"))
            saved.assert_not_called()
        finally:
            storage.close()

    def test_profile_is_available_before_slow_matching_finishes(self):
        with patch.object(api, "_analyse_uploaded_cv_in_background"):
            self.assertEqual(self.upload().status_code, 202)
        entered, release = threading.Event(), threading.Event()

        def slow_matches(*args):
            entered.set()
            if not release.wait(10):
                raise AssertionError("Synthetic matching was never released")
            return {"matched_opportunities": 0, "queued_notifications": 0, "failed_matches": 0}

        with patch.object(api, "analyse_artist", return_value={"identity": {"name": "Synthetic artist"}}), \
             patch.object(api, "extract_cv_headshot", return_value=False), \
             patch.object(api, "_match_artist_against_catalogue", side_effect=slow_matches):
            worker = threading.Thread(target=api._analyse_uploaded_cv_in_background,
                                      args=(self.user_id, str(self.uploads / self.user_id / "cv.txt")))
            worker.start()
            try:
                self.assertTrue(entered.wait(10), "Matching phase was not reached")
                response = self.client.get("/me/cv-analysis?include_profile=false", headers=self.headers)
                self.assertEqual(response.status_code, 200)
                status = response.json()
                self.assertEqual(status["status"], "complete")  # Old Android clients can proceed too.
                self.assertTrue(status["profile_ready"])
                self.assertEqual(status["matching_status"], "processing")
                self.assertNotIn("artist_dna", status)
                self.assertEqual(self.client.get("/me/artist-dna", headers=self.headers).status_code, 200)
                self.assertEqual(self.client.get("/matches", headers=self.headers).json(), [])
                storage = StageVivaStorage(self.database)
                try:
                    self.assertEqual(storage.connection.execute("SELECT COUNT(*) FROM weekly_match_releases").fetchone()[0], 0)
                finally:
                    storage.close()
                with patch.object(api, "_analyse_uploaded_cv_in_background") as duplicate:
                    self.assertEqual(self.upload(b"Must not replace the saved CV").status_code, 202)
                    duplicate.assert_not_called()
            finally:
                release.set()
                worker.join(10)
                self.assertFalse(worker.is_alive())
        final = self.client.get("/me/cv-analysis", headers=self.headers).json()
        self.assertEqual(final["matching_status"], "complete")
        self.assertEqual(final["cv_uploads"]["used"], 1)
        storage = StageVivaStorage(self.database)
        try:
            self.assertEqual(storage.weekly_released_match_ids(self.user_id, api._weekly_release_start(), ["first-real-match"]), {"first-real-match"})
        finally:
            storage.close()

    def test_matching_failure_keeps_profile_and_allowance_without_retrying_cv(self):
        with patch.object(api, "_analyse_uploaded_cv_in_background"):
            self.upload()
        with patch.object(api, "analyse_artist", return_value={"identity": {"name": "Synthetic artist"}}) as analyse, \
             patch.object(api, "extract_cv_headshot", return_value=False), \
             patch.object(api, "_match_artist_against_catalogue", side_effect=RuntimeError("synthetic matching failure")):
            api._analyse_uploaded_cv_in_background(self.user_id, str(self.uploads / self.user_id / "cv.txt"))
            api._finish_uploaded_cv_matches(self.user_id)  # Terminal failures do not retry themselves.
            analyse.assert_called_once()
        status = self.client.get("/me/cv-analysis", headers=self.headers).json()
        self.assertEqual(status["status"], "complete")
        self.assertTrue(status["profile_ready"])
        self.assertEqual(status["matching_status"], "failed")
        self.assertIsNone(status["error"])
        self.assertEqual(status["cv_uploads"]["used"], 1)
        self.assertIsNotNone(status["artist_dna"])

    def test_matching_recovery_uses_saved_profile_without_cv_analysis(self):
        storage = StageVivaStorage(self.database)
        try:
            storage.start_cv_analysis(self.user_id)
            storage.upsert_artist_for_user(self.user_id, {"identity": {"name": "Synthetic artist"}})
            storage.mark_cv_profile_ready(self.user_id)
            self.assertEqual(storage.list_processing_cv_analyses(), [])
            self.assertEqual(storage.list_matching_cv_analyses(), [self.user_id])
        finally:
            storage.close()
        with patch.object(api, "analyse_artist") as analyse, \
             patch.object(api, "_match_artist_against_catalogue", return_value={"failed_matches": 1}) as match:
            api._finish_uploaded_cv_matches(self.user_id)
            match.assert_called_once()
            analyse.assert_not_called()
        status = self.client.get("/me/cv-analysis", headers=self.headers).json()
        self.assertTrue(status["profile_ready"])
        self.assertEqual(status["matching_status"], "failed")

    def test_partial_matching_failure_is_reported_instead_of_claiming_success(self):
        storage = Mock()
        storage.list_opportunity_dnas.return_value = [("one", {}), ("two", {})]
        with patch.object(api, "match_artist_to_opportunity", side_effect=[
            {"overall": {"recommendation": "not_suitable"}}, RuntimeError("synthetic failed match"),
        ]):
            result = api._match_artist_against_catalogue("synthetic-artist", {}, storage)
        self.assertEqual(result["failed_matches"], 1)
        self.assertEqual(result["matched_opportunities"], 1)
        storage.upsert_match.assert_called_once()

    def test_existing_basic_weekly_release_survives_match_preparation(self):
        storage = StageVivaStorage(self.database)
        try:
            week = api._weekly_release_start()
            before = storage.weekly_released_match_ids(self.user_id, week, ["existing-match"])
            storage.start_cv_analysis(self.user_id)
            storage.mark_cv_profile_ready(self.user_id)
            user = storage.get_user(self.user_id)
            self.assertEqual(api._released_match_ids(user, storage, []), before)
        finally:
            storage.close()
