import os
import tempfile
import unittest
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

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
        os.environ["STAGEVIVA_ALLOWED_AI_MODELS"] = "gpt-4o-mini"
        os.environ["STAGEVIVA_AI_REQUESTS_ENABLED"] = "true"
        os.environ["STAGEVIVA_AI_DAILY_REQUEST_LIMIT"] = "2"

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

    def usage_path(self) -> Path:
        return Path(os.environ["STAGEVIVA_AI_USAGE_STATE_PATH"])

    def test_corrupt_or_inconsistent_state_blocks_without_overwriting_it(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        for state in ["{broken", "[]", "{}", json.dumps({"date": today, "count": -1, "operations": {}}),
                      json.dumps({"date": today, "count": 0, "operations": {"test": 2}}),
                      json.dumps({"date": today, "count": True, "operations": {"test": 1}}),
                      json.dumps({"date": today, "count": 0, "operations": {"test": "0"}}),
                      json.dumps({"date": "2099-01-01", "count": 0, "operations": {}})]:
            with self.subTest(state=state):
                self.usage_path().write_text(state, encoding="utf-8")
                with self.assertRaises(AIRequestsDisabledError):
                    reserve_ai_request("gpt-4o-mini", "test")
                self.assertEqual(self.usage_path().read_text(encoding="utf-8"), state)

    def test_missing_initialized_ledger_does_not_restore_allowance(self) -> None:
        reserve_ai_request("gpt-4o-mini", "test")
        self.usage_path().unlink()
        with self.assertRaises(AIRequestsDisabledError):
            reserve_ai_request("gpt-4o-mini", "test")
        self.assertFalse(self.usage_path().exists())

    def test_valid_previous_day_rolls_over(self) -> None:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        self.usage_path().write_text(json.dumps({"date": yesterday, "count": 100, "operations": {"old": 100}}))
        reserve_ai_request("gpt-4o-mini", "new")
        state = json.loads(self.usage_path().read_text())
        self.assertEqual(state["count"], 1)
        self.assertEqual(state["operations"], {"new": 1})

    def test_unreadable_state_and_failed_write_block_without_resetting(self) -> None:
        reserve_ai_request("gpt-4o-mini", "test")
        before = self.usage_path().read_bytes()
        for method in ["read_text", "replace"]:
            with self.subTest(method=method), patch.object(Path, method, side_effect=PermissionError("synthetic")):
                with self.assertRaises(AIRequestsDisabledError):
                    reserve_ai_request("gpt-4o-mini", "test")
            self.assertEqual(self.usage_path().read_bytes(), before)
        self.assertEqual(list(self.usage_path().parent.glob("*.tmp")), [])

    def test_invalid_limit_blocks_instead_of_allowing_one_request(self) -> None:
        for limit in ["0", "-1", "invalid"]:
            with self.subTest(limit=limit), patch.dict(os.environ, {"STAGEVIVA_AI_DAILY_REQUEST_LIMIT": limit}):
                with self.assertRaises(AIRequestsDisabledError):
                    reserve_ai_request("gpt-4o-mini", "test")
        self.assertFalse(self.usage_path().exists())

    def run_worker(self) -> int:
        # Fresh interpreters exercise OS locking and restart persistence, not a
        # shared threading.Lock. No app imports, dotenv, provider or networking.
        worker = """
import sys, socket
def deny(*a, **k): raise RuntimeError('Network disabled')
socket.socket = deny
socket.getaddrinfo = deny
sys.path.insert(0, sys.argv[1])
from ai_safety import reserve_ai_request, AIRequestsDisabledError
try:
    reserve_ai_request('gpt-4o-mini', 'worker')
except AIRequestsDisabledError:
    sys.exit(3)
"""
        result = subprocess.run([sys.executable, "-I", "-c", worker, str(Path(__file__).parent)],
                                capture_output=True, timeout=20)
        self.assertIn(result.returncode, {0, 3}, result.stderr.decode())
        return result.returncode

    def test_competing_processes_share_one_limit(self) -> None:
        os.environ["STAGEVIVA_AI_DAILY_REQUEST_LIMIT"] = "4"
        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(lambda _: self.run_worker(), range(12)))
        self.assertEqual(results.count(0), 4)
        self.assertEqual(results.count(3), 8)
        self.assertEqual(json.loads(self.usage_path().read_text())["count"], 4)
        self.assertEqual(self.run_worker(), 3)
