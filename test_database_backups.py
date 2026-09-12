from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from database_backups import create_database_backup, list_database_backups


class DatabaseBackupTest(unittest.TestCase):
    def test_creates_a_usable_rolling_sqlite_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "stageviva.db"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE profile (name TEXT)")
            connection.execute("INSERT INTO profile VALUES ('Ava')")
            connection.commit()
            connection.close()
            result = create_database_backup(database, keep=2)
            self.assertEqual(result["status"], "completed")
            backups = list_database_backups(database)
            self.assertEqual(len(backups), 1)
            copied = sqlite3.connect(Path(directory) / "backups" / str(backups[0]["name"]))
            try:
                self.assertEqual(copied.execute("SELECT name FROM profile").fetchone()[0], "Ava")
            finally:
                copied.close()
