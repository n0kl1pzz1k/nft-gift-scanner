import json
from pathlib import Path

from db_rowcount_audit import audit_rowcount_usage


def test_audit_reports_rowcount_contexts_without_source_or_data_leaks(tmp_path):
    (tmp_path / "marketplace.py").write_text(
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

    report = audit_rowcount_usage(tmp_path)

    assert report["source_files"] == ["marketplace.py"]
    assert report["rowcount_usage_count"] == 4
    by_context = {item["context"] for item in report["findings"]}
    assert by_context == {"comparison", "return_value", "assignment", "call_argument"}
    assert report["ready_for_postgresql_runtime"] is False
    rendered = json.dumps(report)
    assert "not updated" not in rendered
    assert "logger.info" not in rendered


def test_audit_scans_real_backend_sources_without_parse_errors():
    root = Path(__file__).resolve().parents[1]
    report = audit_rowcount_usage(root)

    assert report["source_files"] == ["marketplace.py", "marketplace_offers.py", "ton_connect.py"]
    assert report["parse_errors"] == []
    assert report["rowcount_usage_count"] >= 5
    assert report["ready_for_postgresql_runtime"] is False


def test_audit_handles_missing_sources(tmp_path):
    report = audit_rowcount_usage(tmp_path)

    assert report["source_files"] == []
    assert report["rowcount_usage_count"] == 0
    assert report["parse_errors"] == []
