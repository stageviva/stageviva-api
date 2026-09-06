"""Initial catalogue for a new StageViva database.

The production service starts with no SQLite records.  A small validated
catalogue gives its first performers opportunities to match against while the
scheduled discovery pipeline builds the live catalogue.
"""

from __future__ import annotations

import json
from pathlib import Path

from opportunity_discovery import DiscoveredOpportunity
from storage import StageVivaStorage


CATALOGUE_PATH = Path(__file__).resolve().with_name("seed_opportunities.json")


def seed_catalogue_if_empty(storage: StageVivaStorage) -> int:
    """Seed only an empty database; never overwrite current listings."""
    if storage.count("opportunities") or not CATALOGUE_PATH.exists():
        return 0

    entries = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    for entry in entries:
        storage.upsert_opportunity(
            DiscoveredOpportunity(**entry["discovery"]),
            entry["opportunity"],
        )
    return len(entries)
