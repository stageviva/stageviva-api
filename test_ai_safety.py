import os
import tempfile
import unittest
from pathlib import Path

from ai_safety import AIRequestsDisabledError, require_ai_requests_enabled, reserve_ai_request


class AIRequestsSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.keys = {
            "STAGEVIVA_AI_REQUESTS_ENABLED",
            "STAGEVIVA_ALLOWED_AI_MODELS",
            "STAGEVIVA_AI_DAILY_REQUEST_LIMIT",
            "STAGEVIVA_AI_USAGE_STATE_PATH",
        }
        self.previous = {key: os.environ.get(key) for key in self.keys}
        os.environ["STAGEVIVA_AI_USAGE_STATE_PATH"] = str(Path(self.directory.name) / "usage.json")

    def tearDown(self) -> None:
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.directory.cleanup()

    def test_ai_requests_are_disabled_by_default(self) -> None:
        os.environ.pop("STAGEVIVA_AI_REQUESTS_ENABLED", None)
        with self.assertRaises(AIRequestsDisabledError):
            require_ai_requests_enabled()

    def test_ai_requests_need_explicit_enablement(self) -> None:
        os.environ["STAGEVIVA_AI_REQUESTS_ENABLED"] = "true"
        require_ai_requests_enabled("gpt-4o-mini")

    def test_unapproved_model_is_blocked(self) -> None:
        os.environ["STAGEVIVA_AI_REQUESTS_ENABLED"] = "true"
        with self.assertRaises(AIRequestsDisabledError):
            require_ai_requests_enabled("gpt-5.6-luna")

    def test_daily_request_limit_is_a_hard_ceiling(self) -> None:
        os.environ["STAGEVIVA_AI_REQUESTS_ENABLED"] = "true"
        os.environ["STAGEVIVA_AI_DAILY_REQUEST_LIMIT"] = "2"
        reserve_ai_request("gpt-4o-mini", "test")
        reserve_ai_request("gpt-4o-mini", "test")
        with self.assertRaises(AIRequestsDisabledError):
            reserve_ai_request("gpt-4o-mini", "test")
