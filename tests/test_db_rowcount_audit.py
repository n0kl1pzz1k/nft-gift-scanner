import json
import tempfile
import unittest
from pathlib import Path

from db_rowcount_audit import audit_rowcount_usage


class RowcountAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_audit_reports_rowcount_contexts_without_source_or_data_leaks(self):
        (self.root / "marketplace.py").write_text(
            """
def update_one(cursor):
    if cursor.rowcount != 1:
        raise RuntimeError("not updated")
def count_rows(cursor):
    return cursor.rowcount
def store_count(cursor):
    changed = cursor.rowcount
    return changed
def log_count(cursor):
    logger.info("changed=%s", cursor.rowcount)
""",
            encoding="utf-8",
        )
        report = audit_rowcount_usage(self.root)
        self.assertEqual(report["source_files"], ["marketplace.py"])
        self.assertEqual(report["rowcount_usage_count"], 4)
        self.assertEqual(
            {item["context"] for item in report["findings"]},
            {"comparison", "return_value", "assignment", "call_argument"},
        )
        self.assertFalse(report["ready_for_postgresql_runtime"])
        rendered = json.dumps(report)
        self.assertNotIn("not updated", rendered)
        self.assertNotIn("logger.info", rendered)

    def test_audit_scans_real_backend_sources_without_parse_errors(self):
        root = Path(__file__).resolve().parents[1]
        report = audit_rowcount_usage(root)
        self.assertEqual(
            report["source_files"],
            ["marketplace.py", "marketplace_offers.py", "ton_connect.py"],
        )
        self.assertEqual(report["parse_errors"], [])
        self.assertGreaterEqual(report["rowcount_usage_count"], 5)
        self.assertFalse(report["ready_for_postgresql_runtime"])

    def test_audit_handles_missing_sources(self):
        report = audit_rowcount_usage(self.root)
        self.assertEqual(report["source_files"], [])
        self.assertEqual(report["rowcount_usage_count"], 0)
        self.assertEqual(report["parse_errors"], [])

    def test_audit_links_rowcount_to_literal_sql_operation_and_comparison(self):
        (self.root / "marketplace.py").write_text(
            """
def cancel_order(conn, order_id):
    updated = conn.execute(
        "UPDATE marketplace_orders SET status = 'cancelled' WHERE id = ? AND status = 'active'",
        (order_id,),
    )
    if updated.rowcount != 1:
        raise RuntimeError("conflict")
""",
            encoding="utf-8",
        )
        report = audit_rowcount_usage(self.root)
        self.assertEqual(report["rowcount_usage_count"], 1)
        finding = report["findings"][0]
        self.assertEqual(finding["sql_operation"], "UPDATE")
        self.assertEqual(finding["comparison_operator"], "NotEq")
        self.assertEqual(finding["context"], "comparison")


if __name__ == "__main__":
    unittest.main()
