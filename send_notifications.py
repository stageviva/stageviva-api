from __future__ import annotations

import argparse
import json

from email_notifications import deliver_pending_emails
from storage import StageVivaStorage


def main() -> None:
    parser = argparse.ArgumentParser(description="Deliver StageViva notification emails.")
    parser.add_argument("--database", default="stageviva.db")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    storage = StageVivaStorage(args.database)
    try:
        print(json.dumps(deliver_pending_emails(storage, limit=args.limit, dry_run=args.dry_run), indent=2))
    finally:
        storage.close()


if __name__ == "__main__":
    main()
