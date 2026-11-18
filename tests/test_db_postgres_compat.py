import os
import unittest

from db_postgres_compat import (
    HybridRow,
    PostgresConnectionAdapter,
    translate_qmark_sql,
)


class QmarkTranslationTests(unittest.TestCase):
    def test_translates_only_placeholders_outside_literals_and_comments(self):
        sql = """SELECT ?, '?', "?", $$?$$, $body$?$body$ /* ? */ -- ?
        FROM items WHERE id = ? AND note = 'it''s ?'"""
        self.assertEqual(
            translate_qmark_sql(sql, ("first", "second")),
            """SELECT %s, '?', "?", $$?$$, $body$?$body$ /* ? */ -- ?
        FROM items WHERE id = %s AND note = 'it''s ?'""",
        )

    def test_preserves_postgres_json_operators(self):
        self.assertEqual(
            translate_qmark_sql("SELECT payload ? 'key', payload ?| array['a'], id = ?", (7,)),
            "SELECT payload ? 'key', payload ?| array['a'], id = %s",
        )

    def test_rejects_parameter_count_mismatch(self):
        with self.assertRaisesRegex(ValueError, "placeholder count mismatch"):
            translate_qmark_sql("SELECT ?, ?", (1,))

    def test_rejects_sqlite_transaction_semantics(self):
        with self.assertRaisesRegex(NotImplementedError, "locking strategy"):
            translate_qmark_sql("BEGIN IMMEDIATE")

    def test_hybrid_row_supports_both_indexing_styles(self):
        row = HybridRow(("id", "name"), (17, "HASSLE"))
        self.assertEqual(row[0], 17)
        self.assertEqual(row["name"], "HASSLE")
        self.assertEqual(row.get("missing"), None)
        self.assertEqual(row.keys(), ("id", "name"))
        self.assertEqual(list(row), [17, "HASSLE"])


class PostgresConnectionAdapterIntegrationTests(unittest.TestCase):
    def test_psycopg_execute_and_hybrid_row(self):
        dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
        if not dsn:
            self.skipTest("isolated PostgreSQL service is not configured")
        import psycopg

        connection = psycopg.connect(dsn, autocommit=True)
        adapter = PostgresConnectionAdapter(connection)
        try:
            row = adapter.execute(
                "SELECT ?::integer AS amount, ?::text AS label",
                (42, "HASSLE NFT"),
            ).fetchone()
            self.assertEqual(row[0], 42)
            self.assertEqual(row["amount"], 42)
            self.assertEqual(row["label"], "HASSLE NFT")
            self.assertEqual(row.keys(), ("amount", "label"))
        finally:
            adapter.close()


if __name__ == "__main__":
    unittest.main()



class PostgresWriteTransactionIntegrationTests(unittest.TestCase):
    def test_rejects_missing_or_blank_lock_keys_before_opening_transaction(self):
        class RecordingConnection:
            def __init__(self):
                self.calls = []

            def execute(self, statement, parameters=()):
                self.calls.append((statement, parameters))

        raw = RecordingConnection()
        adapter = PostgresConnectionAdapter(raw)
        for keys in ((), ("",), ("   ",)):
            with self.subTest(keys=keys):
                with self.assertRaisesRegex(ValueError, "at least one non-empty"):
                    adapter.begin_write(*keys)
        self.assertEqual(raw.calls, [])

    def test_advisory_lock_and_for_update_support(self):
        dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
        if not dsn:
            self.skipTest("isolated PostgreSQL service is not configured")
        import psycopg
        from database import begin_write, select_for_update

        raw = psycopg.connect(dsn, autocommit=True)
        adapter = PostgresConnectionAdapter(raw)
        try:
            begin_write(adapter, "test-order:one", "test-user:two")
            self.assertTrue(adapter.supports_for_update)
            self.assertEqual(
                select_for_update(adapter, "SELECT 1"),
                "SELECT 1 FOR UPDATE",
            )
            adapter.rollback()
        finally:
            adapter.close()

    def test_sqlite_select_is_not_rewritten_for_update(self):
        from database import select_for_update
        self.assertEqual(select_for_update(object(), "SELECT 1"), "SELECT 1")


    def test_for_update_helper_does_not_duplicate_clause(self):
        from database import select_for_update
        class SupportsLocks:
            supports_for_update = True
        self.assertEqual(
            select_for_update(SupportsLocks(), "SELECT 1 FOR UPDATE;"),
            "SELECT 1 FOR UPDATE",
        )


    def test_advisory_lock_serializes_same_business_key(self):
        dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
        if not dsn:
            self.skipTest("isolated PostgreSQL service is not configured")
        import psycopg

        first = psycopg.connect(dsn, autocommit=True)
        second = psycopg.connect(dsn, autocommit=True)
        first_adapter = PostgresConnectionAdapter(first)
        try:
            first_adapter.begin_write("order:lock-test")
            lock_id = PostgresConnectionAdapter._advisory_lock_id("order:lock-test")
            self.assertFalse(
                second.execute("SELECT pg_try_advisory_xact_lock(%s)", (lock_id,)).fetchone()[0]
            )
            first_adapter.rollback()
            self.assertTrue(
                second.execute("SELECT pg_try_advisory_xact_lock(%s)", (lock_id,)).fetchone()[0]
            )
        finally:
            first_adapter.close()
            second.rollback()
            second.close()


    def test_write_transaction_rolls_back_and_commits_through_adapter(self):
        dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
        if not dsn:
            self.skipTest("isolated PostgreSQL service is not configured")
        import uuid
        import psycopg

        table = "adapter_tx_" + uuid.uuid4().hex
        raw = psycopg.connect(dsn, autocommit=True)
        adapter = PostgresConnectionAdapter(raw)
        try:
            adapter.execute(f"CREATE TEMP TABLE {table} (id INTEGER PRIMARY KEY)")
            adapter.begin_write(f"test-adapter-rollback:{table}")
            adapter.execute(f"INSERT INTO {table} (id) VALUES (?)", (1,))
            adapter.rollback()
            rolled_back = adapter.execute(
                f"SELECT COUNT(*) AS count FROM {table}"
            ).fetchone()
            self.assertEqual(rolled_back["count"], 0)

            adapter.begin_write(f"test-adapter-commit:{table}")
            adapter.execute(f"INSERT INTO {table} (id) VALUES (?)", (2,))
            adapter.commit()
            committed = adapter.execute(
                f"SELECT id FROM {table} ORDER BY id"
            ).fetchall()
            self.assertEqual([row["id"] for row in committed], [2])
        finally:
            adapter.close()
