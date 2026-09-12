"""StageViva's central discovery-to-notification pipeline.

This module coordinates existing intelligence modules; it does not contain
matching rules or hand-tuned scores.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from match_service import match_artist_to_opportunity
from opportunity_discovery import (
    DiscoveredOpportunity,
    discover_balletplaces,
    discover_ballee,
    discover_allcasting,
    discover_dance_europe,
    discover_entertainers_worldwide,
    discover_from_source,
)
from opportunity_intelligence import DiscoveryContext, analyse_opportunity
from opportunity_lifecycle import is_current_opportunity
from opportunity_policy import is_stageviva_eligible
from source_registry import Source, get_active_sources
from storage import StageVivaStorage


@dataclass(frozen=True)
class NotificationPolicy:
    """Product policy for creating notification jobs, separate from matching."""

    eligible_recommendations: frozenset[str] = frozenset({
        "strong_match", "good_match",
    })

    def should_notify(self, match: dict[str, Any]) -> bool:
        return match.get("overall", {}).get("recommendation") in self.eligible_recommendations


@dataclass(frozen=True)
class PipelineResult:
    discovered: int
    analysed: int
    stored_opportunities: int
    stored_matches: int
    queued_notifications: int
    skipped_existing: int
    failures: tuple[str, ...]
    skipped_expired: int = 0
    skipped_ineligible: int = 0


def _discovery_context(item: DiscoveredOpportunity) -> DiscoveryContext:
    return DiscoveryContext(item.source_name, item.source_url, item.category, item.title)


def discover_for_source(source: Source) -> list[DiscoveredOpportunity]:
    """Route a source to its tested adapter, with a generic fallback."""

    if source.name == "BalletPlaces":
        return discover_balletplaces()
    if source.name == "Dance Europe":
        return discover_dance_europe()
    if source.name == "Ballee":
        return discover_ballee()
    if source.name == "Entertainers Worldwide":
        return discover_entertainers_worldwide()
    if source.name == "AllCasting":
        return discover_allcasting()
    return discover_from_source(source)


def run_pipeline(
    source: Source,
    artists: Iterable[dict[str, Any]] | None,
    storage: StageVivaStorage,
    *,
    limit: int | None = None,
    discover: Callable[[Source], list[DiscoveredOpportunity]] = discover_for_source,
    analyse: Callable[[str, DiscoveryContext], dict[str, Any]] | None = None,
    match: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] = match_artist_to_opportunity,
    notification_policy: NotificationPolicy = NotificationPolicy(),
) -> PipelineResult:
    """Discover, analyse, store, match, and queue notifications for one source."""

    artist_records = (
        storage.list_registered_artists()
        if artists is None
        else [(storage.upsert_artist(artist), artist) for artist in artists]
    )
    items = discover(source)

    analysed = stored_opportunities = stored_matches = queued = skipped = skipped_expired = skipped_ineligible = 0
    failures: list[str] = []

    for item in items:
        # ``limit`` means newly analysed listings, never merely the first URLs
        # returned by a source. Existing rows must not prevent the next new
        # opportunity from being processed.
        if limit is not None and analysed >= limit:
            break

        if storage.listing_seen(item.listing_url):
            skipped += 1
            continue
        try:
            opportunity = (
                analyse_opportunity(item.listing_url, _discovery_context(item), listing_text=item.description)
                if analyse is None
                else analyse(item.listing_url, _discovery_context(item))
            )
            if not is_current_opportunity(opportunity):
                storage.reject_listing(item.listing_url, item.source_name, "expired")
                skipped_expired += 1
                continue
            if not is_stageviva_eligible(opportunity):
                storage.reject_listing(item.listing_url, item.source_name, "education_or_training")
                skipped_ineligible += 1
                continue
            opportunity_id = storage.upsert_opportunity(item, opportunity)
            analysed += 1
            stored_opportunities += 1
            for artist_id, artist in artist_records:
                result = match(artist, opportunity)
                storage.upsert_match(artist_id, opportunity_id, result)
                stored_matches += 1
                if notification_policy.should_notify(result) and storage.queue_notification(
                    artist_id, opportunity_id, result,
                ):
                    queued += 1
        except Exception as error:
            failures.append(f"{item.listing_url}: {error}")

    return PipelineResult(
        analysed + skipped + skipped_expired + skipped_ineligible + len(failures), analysed, stored_opportunities,
        stored_matches, queued, skipped, tuple(failures), skipped_expired, skipped_ineligible,
    )


def _load_artist(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _find_source(name: str) -> Source:
    for source in get_active_sources():
        if source.name.lower() == name.lower():
            return source
    raise ValueError(f"Unknown active source: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run StageViva's central pipeline for one source.")
    parser.add_argument("--source", default="BalletPlaces")
    parser.add_argument("--artist", action="append")
    parser.add_argument("--database", default="stageviva.db")
    parser.add_argument(
        "--limit", type=int,
        help="Optional safety cap for a manual run. Omit to analyse every new listing discovered.",
    )
    args = parser.parse_args()

    storage = StageVivaStorage(args.database)
    try:
        result = run_pipeline(
            _find_source(args.source),
            ([_load_artist(Path(path)) for path in args.artist] if args.artist else None),
            storage,
            limit=args.limit,
        )
    finally:
        storage.close()
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
