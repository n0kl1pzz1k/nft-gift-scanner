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


    def test_schema_introspection_uses_sqlite_catalog_for_sqlite_connections(self):
        from database import list_database_tables, list_database_columns

        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("CREATE TABLE sample (id TEXT, title TEXT)")
            self.assertEqual(list_database_tables(connection), {"sample"})
            self.assertEqual(list_database_columns(connection, "sample"), {"id", "title"})
        finally:
            connection.close()

    def test_schema_introspection_uses_information_schema_for_postgres_adapter(self):
        from database import list_database_tables, list_database_columns

        class Cursor:
            def __init__(self, rows):
                self.rows = rows

            def fetchall(self):
                return self.rows

        class PostgresLikeConnection:
            supports_for_update = True

            def __init__(self):
                self.calls = []

            def execute(self, statement, parameters=()):
                self.calls.append((statement, parameters))
                if "information_schema.tables" in statement:
                    return Cursor([{"name": "sample"}])
                if "information_schema.columns" in statement:
                    return Cursor([{"name": "id"}, {"name": "title"}])
                raise AssertionError("Unexpected schema query")

        connection = PostgresLikeConnection()
        self.assertEqual(list_database_tables(connection), {"sample"})
        self.assertEqual(list_database_columns(connection, "sample"), {"id", "title"})
        self.assertEqual(connection.calls[1][1], ("sample",))
        self.assertIn("information_schema.columns", connection.calls[1][0])

    def test_schema_introspection_rejects_unsupported_connection(self):
        from database import list_database_tables, list_database_columns

        with self.assertRaisesRegex(RuntimeError, "Unsupported database connection"):
            list_database_tables(object())
        with self.assertRaisesRegex(RuntimeError, "Unsupported database connection"):
            list_database_columns(object(), "sample")

    def test_shared_connect_boundary_accepts_explicit_legacy_path(self):
        from database import connect

        connection = sqlite3.connect(":memory:")
        with patch("database.connect_database", return_value=connection) as sqlite_factory:
            opened = connect("legacy/test.sqlite3")
        self.assertIs(opened, connection)
        sqlite_factory.assert_called_once_with("legacy/test.sqlite3")
        connection.close()

    def test_db_context_forwards_explicit_path_to_shared_factory(self):
        connection = sqlite3.connect(":memory:", isolation_level=None)
        with patch("database.connect", return_value=connection) as shared_factory:
            with db("legacy/test.sqlite3") as yielded:
                self.assertIs(yielded, connection)
            shared_factory.assert_called_once_with("legacy/test.sqlite3")

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



class WriteTransactionBoundaryTests(unittest.TestCase):
    def test_sqlite_begin_write_preserves_immediate_transaction(self):
        from database import begin_write

        connection = sqlite3.connect(":memory:", isolation_level=None)
        try:
            begin_write(connection, "order:123")
            self.assertTrue(connection.in_transaction)
            connection.rollback()
        finally:
            connection.close()

    def test_unsupported_connection_fails_closed(self):
        from database import begin_write

        with self.assertRaisesRegex(RuntimeError, "Unsupported database connection"):
            begin_write(object(), "order:123")



class IntegrityErrorCompatibilityTests(unittest.TestCase):
    def test_sqlite_integrity_error_is_recognized(self):
        from database import is_integrity_error

        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO sample(id) VALUES (1)")
            with self.assertRaises(sqlite3.IntegrityError) as raised:
                connection.execute("INSERT INTO sample(id) VALUES (1)")
            self.assertTrue(is_integrity_error(raised.exception))
        finally:
            connection.close()

    def test_psycopg_integrity_error_is_recognized_without_masking_other_errors(self):
        from database import is_integrity_error

        PgIntegrityError = type("IntegrityError", (Exception,), {"__module__": "psycopg"})
        PgUniqueViolation = type("UniqueViolation", (PgIntegrityError,), {"__module__": "psycopg.errors"})
        PgOperationalError = type("OperationalError", (Exception,), {"__module__": "psycopg"})
        self.assertTrue(is_integrity_error(PgUniqueViolation("duplicate")))
        self.assertFalse(is_integrity_error(PgOperationalError("connection lost")))
        self.assertFalse(is_integrity_error(ValueError("invalid")))

    def test_real_postgres_unique_violation_is_recognized(self):
        import os
        dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
        if not dsn:
            self.skipTest("isolated PostgreSQL service is not configured")
        import psycopg
        from database import is_integrity_error

        with psycopg.connect(dsn) as connection:
            connection.execute("CREATE TEMP TABLE integrity_probe(id INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO integrity_probe(id) VALUES (1)")
            with self.assertRaises(psycopg.IntegrityError) as raised:
                connection.execute("INSERT INTO integrity_probe(id) VALUES (1)")
            self.assertTrue(is_integrity_error(raised.exception))

    def test_profile_insert_conflict_clause_is_supported_by_sqlite(self):
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE TABLE profiles(telegram_id TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL)")
            statement = (
                "INSERT INTO profiles(telegram_id, display_name) VALUES (?, ?) "
                "ON CONFLICT(telegram_id) DO NOTHING"
            )
            connection.execute(statement, ("100", "first"))
            connection.execute(statement, ("100", "second"))
            row = connection.execute(
                "SELECT display_name FROM profiles WHERE telegram_id = ?", ("100",)
            ).fetchone()
            self.assertEqual(row[0], "first")
        finally:
            connection.close()



class StagedPostgresConnectionFactoryTests(unittest.TestCase):
    def test_requires_non_empty_dsn_before_loading_driver(self):
        from database import connect_postgres_database

        for dsn in ("", "   ", None):
            with self.subTest(dsn=dsn):
                with self.assertRaisesRegex(ValueError, "non-empty PostgreSQL DSN"):
                    connect_postgres_database(dsn)

    def test_opens_autocommit_connection_and_wraps_adapter_without_changing_default(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        import database
        from database import connect, connect_postgres_database
        from db_postgres_compat import PostgresConnectionAdapter

        raw = Mock()
        driver_connect = Mock(return_value=raw)
        fake_driver = SimpleNamespace(connect=driver_connect)

        with patch.dict(sys.modules, {"psycopg": fake_driver}):
            adapter = connect_postgres_database("  postgresql://isolated/test  ")

        self.assertIsInstance(adapter, PostgresConnectionAdapter)
        driver_connect.assert_called_once_with(
            "postgresql://isolated/test", autocommit=True
        )
        adapter.close()
        raw.close.assert_called_once()

        # The production factory remains explicitly SQLite-backed.
        with patch("database.connect_database", return_value=sqlite3.connect(":memory:")) as sqlite_factory:
            default_connection = connect()
            try:
                self.assertIsInstance(default_connection, sqlite3.Connection)
                sqlite_factory.assert_called_once_with(database.DB_PATH)
            finally:
                default_connection.close()
