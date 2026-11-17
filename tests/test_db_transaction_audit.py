import json
import tempfile
import unittest
from pathlib import Path

from db_transaction_audit import audit_transaction_boundaries


class DatabaseTransactionAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_audit_classifies_write_and_transaction_boundaries_without_source_leaks(self):
        (self.root / "marketplace.py").write_text(
            """
def atomic_update(conn):
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE listings SET status = 'cancelled' WHERE id = ?", (item_id,))
    conn.execute("INSERT INTO events(id) VALUES (?)", (event_id,))
    conn.commit()

def autocommit_write(conn):
    conn.execute("INSERT INTO favorites(id) VALUES (?)", (item_id,))

def multi_write_without_begin(conn):
    conn.execute("UPDATE listings SET status = 'matched'")
    conn.execute("INSERT INTO events(id) VALUES ('event')")

def dynamic_insert(conn, table):
    conn.execute(f"INSERT INTO {table}(id) VALUES (?)", (item_id,))

def script_migration(conn):
    conn.executescript("CREATE TABLE sample(id TEXT); INSERT INTO sample VALUES ('x');")
""",
            encoding="utf-8",
        )

        report = audit_transaction_boundaries(self.root)

        self.assertEqual(report["source_files"], ["marketplace.py"])
        self.assertFalse(report["ready_for_postgresql_runtime"])
        self.assertEqual(report["function_count"], 5)
        by_name = {item["function"]: item for item in report["functions"]}
        self.assertEqual(by_name["atomic_update"]["transaction_start_count"], 1)
        self.assertEqual(by_name["atomic_update"]["commit_count"], 1)
        self.assertEqual(by_name["atomic_update"]["mutation_count"], 2)
        self.assertTrue(by_name["autocommit_write"]["single_statement_autocommit_write"])
        self.assertTrue(by_name["multi_write_without_begin"]["multi_statement_mutation_without_explicit_begin"])
        self.assertEqual(by_name["dynamic_insert"]["dynamic_sql_count"], 1)
        self.assertEqual(by_name["dynamic_insert"]["mutation_kinds"], ["INSERT"])
        self.assertEqual(by_name["script_migration"]["executescript_count"], 1)
        self.assertEqual(by_name["script_migration"]["mutation_count"], 2)

        rendered = json.dumps(report)
        self.assertNotIn("item_id", rendered)
        self.assertNotIn("event_id", rendered)
        self.assertNotIn("CREATE TABLE sample", rendered)

    def test_audit_handles_missing_sources_and_reports_no_false_readiness(self):
        report = audit_transaction_boundaries(self.root)

        self.assertEqual(report["source_files"], [])
        self.assertEqual(report["function_count"], 0)
        self.assertFalse(report["ready_for_postgresql_runtime"])


if __name__ == "__main__":
    unittest.main()
