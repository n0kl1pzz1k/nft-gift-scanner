import json
from pathlib import Path

from db_sql_compat_audit import SQLITE_FALLBACK_FUNCTIONS, audit_source_tree


def test_audit_finds_sqlite_runtime_assumptions_without_source_or_data_leaks(tmp_path):
    (tmp_path / "app.py").write_text(
        "SELECT name FROM sqlite_master WHERE type = 'table'\n"
        "PRAGMA table_info(verified_wallets)\n",
        encoding="utf-8",
    )
    (tmp_path / "marketplace.py").write_text(
        "conn = sqlite3.connect(path)\n"
        "conn.execute('BEGIN IMMEDIATE')\n"
        "conn.execute('PRAGMA foreign_keys = ON')\n"
        "conn.execute('INSERT OR IGNORE INTO items(id) VALUES (?)', (item_id,))\n"
        "cursor = conn.execute('INSERT INTO items DEFAULT VALUES')\n"
        "new_id = cursor.lastrowid\n"
        """conn.executescript("CREATE TRIGGER guard BEFORE INSERT ON x BEGIN SELECT RAISE(ABORT, 'bad'); END")\n"""
        "lower(hex(randomblob(8)))\n",
        encoding="utf-8",
    )
    (tmp_path / "marketplace_offers.py").write_text(
        "conn.executescript('CREATE TABLE x(id INTEGER PRIMARY KEY AUTOINCREMENT)')\n"
        "SELECT rowid AS sequence FROM marketplace_deal_events\n",
        encoding="utf-8",
    )
    (tmp_path / "ton_connect.py").write_text(
        "conn.row_factory = sqlite3.Row\n",
        encoding="utf-8",
    )

    report = audit_source_tree(tmp_path)

    assert report["source_files"] == ["app.py", "marketplace.py", "marketplace_offers.py", "ton_connect.py"]
    assert report["ready_for_postgresql_runtime"] is False
    assert report["blocker_count"] >= 8
    adapter_findings = [
        item for item in report["findings"]
        if item["severity"] == "postgres_adapter"
    ]
    assert {
        "sqlite_qmark_parameters",
        "sqlite_autoincrement",
        "sqlite_row_factory",
    } <= {item["feature"] for item in adapter_findings}
    features = {item["feature"] for item in report["findings"]}
    assert {
        "sqlite_connection_factory",
        "sqlite_transaction_lock",
        "sqlite_pragma",
        "sqlite_master_catalog",
        "sqlite_insert_or_ignore",
        "sqlite_qmark_parameters",
        "sqlite_lastrowid",
        "sqlite_script_execution",
        "sqlite_autoincrement",
        "sqlite_row_factory",
        "sqlite_rowid",
        "sqlite_trigger_abort",
        "sqlite_randomblob_id",
    } <= features
    app_findings = [item for item in report["findings"] if item["file"] == "app.py"]
    assert {item["feature"] for item in app_findings} == {"sqlite_master_catalog", "sqlite_pragma"}
    rendered = json.dumps(report)
    assert "item_id" not in rendered
    assert "CREATE TABLE x" not in rendered


def test_audit_handles_multiline_sql_and_ignores_python_comments(tmp_path):
    (tmp_path / "marketplace.py").write_text(
        "# sqlite3.connect(comment_only)\n"
        "connection = sqlite3\n"
        "    .connect(\n"
        "        database_path\n"
        "    )\n"
        'query = """INSERT\n'
        "    OR IGNORE INTO verified_wallets(address)\n"
        "    VALUES (?)\n"
        '"""\n'
        "connection.execute(query)\n",
        encoding="utf-8",
    )

    report = audit_source_tree(tmp_path)
    findings = report["findings"]

    assert not any(item["feature"] == "sqlite_connection_factory" and item["line"] == 1 for item in findings)
    assert any(item["feature"] == "sqlite_connection_factory" and item["line"] == 2 for item in findings)
    assert any(item["feature"] == "sqlite_insert_or_ignore" and item["line"] == 6 for item in findings)
    assert any(item["feature"] == "sqlite_qmark_parameters" and item["line"] == 8 for item in findings)
    assert report["ready_for_postgresql_runtime"] is False


def test_audit_handles_missing_sources_and_does_not_claim_readiness(tmp_path):
    report = audit_source_tree(tmp_path)

    assert report["source_files"] == []
    assert report["finding_count"] == 0
    assert report["ready_for_postgresql_runtime"] is False



def test_audit_ignores_python_docstrings_but_scans_executable_sql_literals(tmp_path):
    (tmp_path / "marketplace.py").write_text(
        '"""Historical note: BEGIN IMMEDIATE and PRAGMA are discussed here, not executed."""\n'
        "def initialize():\n"
        '    """Example only: conn.executescript("CREATE TABLE demo(id INTEGER)")"""\n'
        '    conn.execute("PRAGMA foreign_keys = ON")\n',
        encoding="utf-8",
    )

    report = audit_source_tree(tmp_path)
    findings = report["findings"]

    assert not any(item["feature"] == "sqlite_transaction_lock" for item in findings)
    assert not any(item["feature"] == "sqlite_script_execution" for item in findings)
    assert any(item["feature"] == "sqlite_pragma" and item["line"] == 4 for item in findings)



def test_repository_sqlite_only_code_is_confined_to_explicit_fallback_functions():
    repository_root = Path(__file__).resolve().parents[1]
    report = audit_source_tree(repository_root)

    assert report["blocker_count"] == 0, report["findings"]
    assert report["static_blockers_clear"] is True
    assert report["sqlite_fallback_count"] >= 34
    assert report["postgres_adapter_count"] >= 327
    assert report["review_count"] == 0
    assert report["ready_for_postgresql_runtime"] is True

    fallback_functions = {
        (item["file"], item["function"])
        for item in report["findings"]
        if item["severity"] == "sqlite_fallback"
    }
    assert fallback_functions
    assert fallback_functions <= SQLITE_FALLBACK_FUNCTIONS
