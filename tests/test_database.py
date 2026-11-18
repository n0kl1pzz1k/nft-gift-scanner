import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import connect_database, db


class DatabaseConnectionBoundaryTests(unittest.TestCase):
    def test_connection_preserves_existing_sqlite_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nested" / "market.sqlite3"
            connection = connect_database(path)
            try:
                self.assertTrue(path.parent.is_dir())
                self.assertIsInstance(connection, sqlite3.Connection)
                self.assertIs(connection.row_factory, sqlite3.Row)
                self.assertIsNone(connection.isolation_level)
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
                self.assertEqual(connection.execute("PRAGMA synchronous").fetchone()[0], 2)
                self.assertEqual(connection.execute("PRAGMA temp_store").fetchone()[0], 2)

                connection.execute("CREATE TABLE parent(id TEXT PRIMARY KEY)")
                connection.execute(
                    "CREATE TABLE child(parent_id TEXT REFERENCES parent(id))"
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("INSERT INTO child(parent_id) VALUES ('missing')")
            finally:
                connection.close()

    def test_db_context_closes_connection_without_adding_transaction_semantics(self):
        connection = sqlite3.connect(":memory:", isolation_level=None)
        with patch("database.connect", return_value=connection):
            with db() as yielded:
                self.assertIs(yielded, connection)
                self.assertFalse(yielded.in_transaction)
                yielded.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY)")
                yielded.execute("INSERT INTO sample DEFAULT VALUES")
                self.assertFalse(yielded.in_transaction)
                self.assertEqual(yielded.execute("SELECT COUNT(*) FROM sample").fetchone()[0], 1)

        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()


class DatabaseBackendConfigurationTests(unittest.TestCase):
    def test_database_url_without_explicit_sqlite_selection_fails_closed(self):
        from database import validate_database_configuration

        secret = "postgresql://user:private-password@example.invalid/db"
        with self.assertRaisesRegex(RuntimeError, "DATABASE_URL is configured") as raised:
            validate_database_configuration({"DATABASE_URL": secret})
        self.assertNotIn("private-password", str(raised.exception))

    def test_explicit_sqlite_selection_is_supported_even_if_database_url_exists(self):
        from database import validate_database_configuration

        self.assertEqual(
            validate_database_configuration({
                "DATABASE_URL": "postgresql://unused",
                "HASSLE_NFT_DB_BACKEND": "sqlite",
            }),
            "sqlite",
        )

    def test_unsupported_backend_selection_fails_closed_without_echoing_dsn(self):
        from database import validate_database_configuration

        secret = "postgresql://private-password@example.invalid/db"
        with self.assertRaisesRegex(RuntimeError, "not supported") as raised:
            validate_database_configuration({
                "HASSLE_NFT_DB_BACKEND": "postgres",
                "DATABASE_URL": secret,
            })
        self.assertNotIn("private-password", str(raised.exception))
