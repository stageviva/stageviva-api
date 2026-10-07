import os
import unittest

from ai_safety import AIRequestsDisabledError, require_ai_requests_enabled


class AIRequestsSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get("STAGEVIVA_AI_REQUESTS_ENABLED")

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("STAGEVIVA_AI_REQUESTS_ENABLED", None)
        else:
            os.environ["STAGEVIVA_AI_REQUESTS_ENABLED"] = self.previous

    def test_ai_requests_are_disabled_by_default(self) -> None:
        os.environ.pop("STAGEVIVA_AI_REQUESTS_ENABLED", None)
        with self.assertRaises(AIRequestsDisabledError):
            require_ai_requests_enabled()

    def test_ai_requests_need_explicit_enablement(self) -> None:
        os.environ["STAGEVIVA_AI_REQUESTS_ENABLED"] = "true"
        require_ai_requests_enabled()
