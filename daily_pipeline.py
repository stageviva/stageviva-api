"""Run every validated StageViva source once; schedule this command daily."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable, Iterable

from source_registry import Source, get_automated_sources
from stageviva_pipeline import PipelineResult, run_pipeline
from storage import StageVivaStorage


def run_daily_pipeline(
    storage: StageVivaStorage,
    *,
    sources: Iterable[Source] | None = None,
    limit_per_source: int | None = None,
    runner: Callable[..., PipelineResult] = run_pipeline,
) -> dict[str, object]:
    """Run each tested source independently, recording both success and failure."""
    selected_sources = list(sources if sources is not None else get_automated_sources())
    runs: list[dict[str, object]] = []
    for source in selected_sources:
        started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        try:
            result = runner(source, None, storage, limit=limit_per_source)
            result_data = asdict(result)
            storage.record_source_run(source.name, started_at, result_data)
            runs.append({"source": source.name, "status": "completed", "result": result_data})
        except Exception as error:
            message = str(error)
            result_data = {"discovered": 0, "analysed": 0, "stored_opportunities": 0,
                           "stored_matches": 0, "queued_notifications": 0,
                           "skipped_existing": 0, "skipped_expired": 0, "failures": [message]}
            storage.record_source_run(source.name, started_at, result_data, error=message)
            runs.append({"source": source.name, "status": "failed", "error": message})
    return {"sources_run": len(selected_sources), "runs": runs}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run StageViva's validated sources once.")
    parser.add_argument("--database", default="stageviva.db")
    parser.add_argument(
        "--limit-per-source", type=int,
        help="Optional safety cap for a manual run. Omit to analyse every new listing discovered.",
    )
    args = parser.parse_args()
    storage = StageVivaStorage(args.database)
    try:
        print(json.dumps(run_daily_pipeline(storage, limit_per_source=args.limit_per_source), indent=2, ensure_ascii=False))
    finally:
        storage.close()


if __name__ == "__main__":
    main()
