import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db_backup import backup_database


class DatabaseBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source.sqlite3"
        self.destination = self.root / "backups" / "hassle-snapshot.sqlite3"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_source(self):
        connection = sqlite3.connect(self.source)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                telegram_id TEXT NOT NULL UNIQUE
            );
            CREATE TABLE listings (
                id INTEGER PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id),
                title TEXT NOT NULL
            );
            INSERT INTO users(id, telegram_id) VALUES (1, 'user-1'), (2, 'user-2');
            INSERT INTO listings(id, owner_id, title) VALUES (10, 1, 'Gift A'), (11, 2, 'Gift B');
            """
        )
        connection.commit()
        connection.close()

    def test_backup_is_consistent_and_manifest_is_verifiable(self):
        self._create_source()

        manifest = backup_database(self.source, self.destination)
        manifest_path = Path(str(self.destination) + ".manifest.json")
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["integrity_check"], "ok")
        self.assertEqual(manifest["foreign_key_violations"], 0)
        self.assertEqual(manifest["table_count"], 2)
        self.assertEqual(manifest["total_rows"], 4)
        self.assertEqual(manifest["tables"], {"listings": 2, "users": 2})
        self.assertEqual(loaded["backup_sha256"], manifest["backup_sha256"])
        self.assertEqual(
            hashlib.sha256(self.destination.read_bytes()).hexdigest(),
            manifest["backup_sha256"],
        )

        snapshot = sqlite3.connect(self.destination)
        self.assertEqual(snapshot.execute("SELECT COUNT(*) FROM listings").fetchone()[0], 2)
        self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(snapshot.execute("PRAGMA foreign_key_check").fetchall(), [])
        snapshot.close()

    def test_manifest_preserves_autoincrement_high_water_marks(self):
        connection = sqlite3.connect(self.source)
        connection.execute(
            "CREATE TABLE audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO audit_events(event) VALUES ('created')")
        connection.execute("INSERT INTO audit_events(event) VALUES ('deleted later')")
        connection.execute("DELETE FROM audit_events WHERE id = 2")
        connection.commit()
        connection.close()

        manifest = backup_database(self.source, self.destination)

        self.assertEqual(manifest["sequence_counters"], {"audit_events": 2})
        snapshot = sqlite3.connect(self.destination)
        try:
            self.assertEqual(
                snapshot.execute("SELECT COALESCE(MAX(id), 0) FROM audit_events").fetchone()[0],
                1,
            )
            self.assertEqual(
                snapshot.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name = 'audit_events'"
                ).fetchone()[0],
                2,
            )
        finally:
            snapshot.close()

    def test_backup_rejects_source_with_foreign_key_violations(self):
        connection = sqlite3.connect(self.source)
        connection.executescript(
            """
            CREATE TABLE users (id INTEGER PRIMARY KEY);
            CREATE TABLE listings (
                id INTEGER PRIMARY KEY,
                owner_id INTEGER REFERENCES users(id)
            );
            INSERT INTO listings(id, owner_id) VALUES (1, 999);
            """
        )
        connection.commit()
        connection.close()

        with self.assertRaisesRegex(RuntimeError, "foreign-key check failed"):
            backup_database(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_backup_rejects_overwriting_the_live_source(self):
        self._create_source()
        with self.assertRaisesRegex(ValueError, "must differ"):
            backup_database(self.source, self.source)
        self.assertTrue(self.source.exists())


if __name__ == "__main__":
    unittest.main()
