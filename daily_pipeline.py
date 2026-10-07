"""Run every validated StageViva source once; schedule this command daily."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable, Iterable

from source_registry import Source, get_automated_sources
from stageviva_pipeline import PipelineResult, run_pipeline
from storage import StageVivaStorage


def _safe_error_message(error: Exception) -> str:
    """Remove credentials if a dependency includes them in an exception."""
    return re.sub(r"(access_token=)[^&\s]+", r"\1[redacted]", str(error), flags=re.IGNORECASE)


def run_daily_pipeline(
    storage: StageVivaStorage,
    *,
    sources: Iterable[Source] | None = None,
    limit_per_source: int | None = None,
    total_analysis_limit: int | None = None,
    runner: Callable[..., PipelineResult] = run_pipeline,
) -> dict[str, object]:
    """Run tested sources with per-source and total AI-analysis safety caps."""
    selected_sources = list(sources if sources is not None else get_automated_sources())
    runs: list[dict[str, object]] = []
    total_analysed = 0
    for source in selected_sources:
        remaining = None if total_analysis_limit is None else total_analysis_limit - total_analysed
        if remaining is not None and remaining <= 0:
            break
        started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        try:
            source_limit = limit_per_source
            if source.per_run_limit is not None:
                source_limit = (
                    source.per_run_limit
                    if source_limit is None
                    else min(source_limit, source.per_run_limit)
                )
            if remaining is not None:
                source_limit = remaining if source_limit is None else min(source_limit, remaining)
            result = runner(source, None, storage, limit=source_limit)
            total_analysed += result.analysed
            result_data = asdict(result)
            storage.record_source_run(source.name, started_at, result_data)
            runs.append({"source": source.name, "status": "completed", "result": result_data})
        except Exception as error:
            message = _safe_error_message(error)
            result_data = {"discovered": 0, "analysed": 0, "stored_opportunities": 0,
                           "stored_matches": 0, "queued_notifications": 0,
                           "skipped_existing": 0, "skipped_expired": 0, "failures": [message]}
            storage.record_source_run(source.name, started_at, result_data, error=message)
            runs.append({"source": source.name, "status": "failed", "error": message})
    return {"sources_run": len(runs), "analysed": total_analysed, "runs": runs}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run StageViva's validated sources once.")
    parser.add_argument("--database", default="stageviva.db")
    parser.add_argument(
        "--limit-per-source", type=int,
        help="Optional safety cap for a manual run. Omit to analyse every new listing discovered.",
    )
    parser.add_argument(
        "--total-analysis-limit", type=int,
        help="Optional total cap across every source in this run.",
    )
    args = parser.parse_args()
    storage = StageVivaStorage(args.database)
    try:
        print(json.dumps(run_daily_pipeline(
            storage,
            limit_per_source=args.limit_per_source,
            total_analysis_limit=args.total_analysis_limit,
        ), indent=2, ensure_ascii=False))
    finally:
        storage.close()


if __name__ == "__main__":
    main()
