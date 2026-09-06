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


if __name__ == "__main__":
    unittest.main()
