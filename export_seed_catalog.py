"""Export a privacy-safe opportunity-only seed catalogue for a fresh deployment.

This is a release utility. It intentionally exports no users, profiles, CVs or
match results from the local SQLite database.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE_DB = ROOT / "stageviva.db"
OUTPUT = ROOT / "seed_opportunities.json"


def main() -> None:
    connection = sqlite3.connect(SOURCE_DB)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """SELECT title, listing_url, source_name, source_url, category, opportunity_json
               FROM opportunities WHERE visible = 1 ORDER BY title""",
        ).fetchall()
    finally:
        connection.close()

    catalogue = [
        {
            "discovery": {
                key: row[key]
                for key in ("title", "listing_url", "source_name", "source_url", "category")
            },
            "opportunity": json.loads(row["opportunity_json"]),
        }
        for row in rows
    ]
    OUTPUT.write_text(json.dumps(catalogue, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported {len(catalogue)} opportunities to {OUTPUT.name}")


if __name__ == "__main__":
    main()
