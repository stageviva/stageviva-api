"""Read-only public website adapter check: no AI, database writes or notifications.

Instagram is deliberately excluded: use the authenticated Admin operations
screen to review Meta configuration and the production run history.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json

from source_registry import get_active_sources
from stageviva_pipeline import discover_for_source


def check_source(source):
    try:
        items = discover_for_source(source)
        return {"source": source.name, "status": "found" if items else "empty",
                "candidates": len(items), "sample_urls": [item.listing_url for item in items[:2]]}
    except Exception as error:
        return {"source": source.name, "status": "failed", "error": str(error)}


def main():
    sources = [source for source in get_active_sources()
               if source.automation_ready and source.source_type == "web"]
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(check_source, source) for source in sources]
        results = [future.result() for future in as_completed(futures)]
    print(json.dumps(sorted(results, key=lambda row: row["source"]), indent=2))


if __name__ == "__main__":
    main()
