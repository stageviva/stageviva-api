from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from daily_pipeline import run_daily_pipeline
from source_registry import Source
from stageviva_pipeline import PipelineResult
from storage import StageVivaStorage


class DailyPipelineTest(unittest.TestCase):
    def test_records_success_and_failure_without_stopping_other_sources(self) -> None:
        good = Source("Good", "https://example.test/good", "dance", automation_ready=True)
        bad = Source("Bad", "https://example.test/bad", "dance", automation_ready=True)

        def runner(source, _artists, _storage, *, limit):
            if source.name == "Bad":
                raise RuntimeError("Source unavailable")
            return PipelineResult(1, 1, 1, 0, 0, 0, ())

        with tempfile.TemporaryDirectory() as directory:
            storage = StageVivaStorage(Path(directory) / "stageviva.db")
            try:
                result = run_daily_pipeline(storage, sources=[good, bad], runner=runner)
                self.assertEqual(result["sources_run"], 2)
                self.assertEqual([row["status"] for row in storage.list_source_runs()], ["failed", "completed"])
            finally:
                storage.close()

    def test_source_limit_can_lower_global_daily_limit(self) -> None:
        instagram = Source(
            "Instagram", "https://example.test", "dance",
            automation_ready=True, per_run_limit=1,
        )
        seen_limits: list[int | None] = []

        def runner(_source, _artists, _storage, *, limit):
            seen_limits.append(limit)
            return PipelineResult(0, 0, 0, 0, 0, 0, ())

        with tempfile.TemporaryDirectory() as directory:
            storage = StageVivaStorage(Path(directory) / "stageviva.db")
            try:
                run_daily_pipeline(storage, sources=[instagram], runner=runner, limit_per_source=5)
            finally:
                storage.close()
        self.assertEqual(seen_limits, [1])

    def test_failure_redacts_an_access_token_before_recording(self) -> None:
        source = Source("Source", "https://example.test", "dance", automation_ready=True)

        def runner(_source, _artists, _storage, *, limit):
            raise RuntimeError("request failed: https://example.test/?access_token=secret-value")

        with tempfile.TemporaryDirectory() as directory:
            storage = StageVivaStorage(Path(directory) / "stageviva.db")
            try:
                run_daily_pipeline(storage, sources=[source], runner=runner)
                run = storage.list_source_runs()[0]
            finally:
                storage.close()
        self.assertNotIn("secret-value", str(run))
        self.assertIn("[redacted]", str(run))


if __name__ == "__main__":
    unittest.main()
