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

    def test_audit_scans_real_backend_sources_without_parse_errors(self):
        repository_root = Path(__file__).resolve().parents[1]

        report = audit_transaction_boundaries(repository_root)

        self.assertEqual(
            report["source_files"],
            ["app.py", "marketplace.py", "marketplace_offers.py", "ton_connect.py"],
        )
        self.assertEqual(report["parse_errors"], [])
        self.assertGreater(report["mutation_function_count"], 0)
        self.assertFalse(report["ready_for_postgresql_runtime"])

    def test_cte_writes_and_sql_transaction_end_statements_are_counted(self):
        (self.root / 'marketplace.py').write_text("def cte_write(conn):\\n    conn.execute(\\\"WITH pending AS (SELECT id FROM listings) UPDATE listings SET status = 'sold' WHERE id IN (SELECT id FROM pending)\\\")\\ndef sql_transaction_end(conn):\\n    conn.execute('BEGIN IMMEDIATE')\\n    conn.execute(\\\"INSERT INTO events(id) VALUES ('x')\\\")\\n    conn.execute('COMMIT')\\ndef sql_rollback(conn):\\n    conn.execute('BEGIN')\\n    conn.executescript(\\\"UPDATE listings SET status = 'x'; ROLLBACK;\\")\\n", encoding='utf-8')
        report = audit_transaction_boundaries(self.root)
        by_name = {item['function']: item for item in report['functions']}
        self.assertEqual(by_name['cte_write']['mutation_count'], 1)
        self.assertEqual(by_name['cte_write']['mutation_kinds'], ['UPDATE'])
        self.assertEqual(by_name['sql_transaction_end']['commit_count'], 1)
        self.assertFalse(by_name['sql_transaction_end']['transaction_without_explicit_end'])
        self.assertEqual(by_name['sql_rollback']['rollback_count'], 1)
        self.assertFalse(by_name['sql_rollback']['transaction_without_explicit_end'])

    def test_sql_comment_markers_inside_string_literals_do_not_hide_statements(self):
        (self.root / 'marketplace.py').write_text("def two_writes_with_comment_text(conn):\\n    conn.execute(\\\"INSERT INTO notes(value) VALUES ('note -- tag'); DELETE FROM notes WHERE id = 1\\\")\\n", encoding='utf-8')
        report = audit_transaction_boundaries(self.root)
        item = report['functions'][0]
        self.assertEqual(item['mutation_count'], 2)
        self.assertEqual(item['mutation_kinds'], ['INSERT', 'DELETE'])
        self.assertTrue(item['multi_statement_mutation_without_explicit_begin'])

    def test_audit_handles_missing_sources_and_reports_no_false_readiness(self):
        report = audit_transaction_boundaries(self.root)

        self.assertEqual(report["source_files"], [])
        self.assertEqual(report["function_count"], 0)
        self.assertFalse(report["ready_for_postgresql_runtime"])


if __name__ == "__main__":
    unittest.main()
