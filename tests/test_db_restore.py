import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db_backup import backup_database
from db_restore import restore_database


class DatabaseRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.sqlite3"
        self.backup = self.root / "backup.sqlite3"
        self.manifest = Path(str(self.backup) + ".manifest.json")
        self.restored = self.root / "restored" / "restored.sqlite3"
        connection = sqlite3.connect(self.source)
        connection.executescript("""
            CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, handle TEXT NOT NULL UNIQUE);
            CREATE TABLE gifts(id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id));
            INSERT INTO users(handle) VALUES ('alpha'), ('beta');
            INSERT INTO gifts VALUES (10, 1), (11, 2);
            INSERT INTO users(handle) VALUES ('deleted-sequence');
            DELETE FROM users WHERE handle='deleted-sequence';
        """)
        connection.commit()
        connection.close()
        backup_database(self.source, self.backup)

    def tearDown(self):
        self.temp.cleanup()

    def test_restore_rehearsal_verifies_rows_schema_foreign_keys_and_sequences(self):
        report = restore_database(self.backup, self.manifest, self.restored)
        self.assertTrue(report["restored"])
        self.assertEqual(report["integrity_check"], "ok")
        self.assertEqual(report["foreign_key_violations"], 0)
        self.assertEqual(report["table_count"], 2)
        self.assertEqual(report["total_rows"], 4)
        self.assertEqual(report["sequence_counters"], {"users": 3})
        connection = sqlite3.connect(self.restored)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM gifts").fetchone()[0], 2)
        self.assertEqual(connection.execute("SELECT seq FROM sqlite_sequence WHERE name='users'").fetchone()[0], 3)
        connection.close()


    def test_restore_inspection_detects_changed_row_content_with_same_row_count(self):
        from db_restore import _inspect

        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES (1, 'original')")
        before = _inspect(connection)
        connection.execute("UPDATE sample SET value = 'changed' WHERE id = 1")
        after = _inspect(connection)
        connection.close()

        self.assertEqual(before["tables"], after["tables"])
        self.assertEqual(before["schema_sha256"], after["schema_sha256"])
        self.assertNotEqual(before["table_sha256"], after["table_sha256"])

    def test_restore_rejects_tampered_backup(self):
        with self.backup.open("ab") as stream:
            stream.write(b"tamper")
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            restore_database(self.backup, self.manifest, self.restored)
        self.assertFalse(self.restored.exists())

    def test_restore_never_overwrites_existing_destination(self):
        self.restored.parent.mkdir(parents=True)
        self.restored.write_bytes(b"keep-me")
        with self.assertRaises(FileExistsError):
            restore_database(self.backup, self.manifest, self.restored)
        self.assertEqual(self.restored.read_bytes(), b"keep-me")


if __name__ == "__main__":
    unittest.main()
