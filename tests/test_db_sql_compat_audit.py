import json

from db_sql_compat_audit import audit_source_tree


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
        "conn.executescript('CREATE TRIGGER guard BEFORE INSERT ON x BEGIN SELECT RAISE(ABORT, \\"bad\\"); END')\n"
        "lower(hex(randomblob(8)))\n",
        encoding="utf-8",
    )
    (tmp_path / "demo_offers.py").write_text(
        "conn.executescript('CREATE TABLE x(id INTEGER PRIMARY KEY AUTOINCREMENT)')\n"
        "SELECT rowid AS sequence FROM demo_deal_events\n",
        encoding="utf-8",
    )
    (tmp_path / "ton_connect.py").write_text(
        "conn.row_factory = sqlite3.Row\n",
        encoding="utf-8",
    )

    report = audit_source_tree(tmp_path)

    assert report["source_files"] == ["app.py", "marketplace.py", "demo_offers.py", "ton_connect.py"]
    assert report["ready_for_postgresql_runtime"] is False
    assert report["blocker_count"] >= 8
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


def test_audit_handles_missing_sources_and_does_not_claim_readiness(tmp_path):
    report = audit_source_tree(tmp_path)

    assert report["source_files"] == []
    assert report["finding_count"] == 0
    assert report["ready_for_postgresql_runtime"] is False
