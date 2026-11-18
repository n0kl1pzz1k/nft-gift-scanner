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
