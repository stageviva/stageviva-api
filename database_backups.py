"""Small, dependency-free SQLite backups for the StageViva production disk."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def create_database_backup(database_path: str | Path, *, keep: int = 14) -> dict[str, str | int]:
    """Snapshot a live SQLite database and retain a short rolling history."""
    source_path = Path(database_path)
    if not source_path.exists():
        return {"status": "skipped", "reason": "database_not_created"}
    backup_directory = source_path.parent / "backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination_path = backup_directory / f"stageviva-{timestamp}.db"
    source = sqlite3.connect(source_path)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()

    snapshots = sorted(backup_directory.glob("stageviva-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    for stale_snapshot in snapshots[max(1, keep):]:
        stale_snapshot.unlink()
    return {"status": "completed", "path": str(destination_path), "retained": min(len(snapshots), max(1, keep))}


def list_database_backups(database_path: str | Path) -> list[dict[str, str | int]]:
    """Return metadata only; the owner dashboard never exposes database contents."""
    backup_directory = Path(database_path).parent / "backups"
    if not backup_directory.is_dir():
        return []
    return [{
        "name": snapshot.name,
        "created_at": datetime.fromtimestamp(snapshot.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat(),
        "bytes": snapshot.stat().st_size,
    } for snapshot in sorted(backup_directory.glob("stageviva-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)]
