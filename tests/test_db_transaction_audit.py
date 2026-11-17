import json

from db_transaction_audit import audit_transaction_boundaries


def test_audit_classifies_write_and_transaction_boundaries_without_source_leaks(tmp_path):
    (tmp_path / "marketplace.py").write_text(
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

    report = audit_transaction_boundaries(tmp_path)

    assert report["source_files"] == ["marketplace.py"]
    assert report["ready_for_postgresql_runtime"] is False
    assert report["function_count"] == 5
    by_name = {item["function"]: item for item in report["functions"]}
    assert by_name["atomic_update"]["transaction_start_count"] == 1
    assert by_name["atomic_update"]["commit_count"] == 1
    assert by_name["atomic_update"]["mutation_count"] == 2
    assert by_name["autocommit_write"]["single_statement_autocommit_write"] is True
    assert by_name["multi_write_without_begin"]["multi_statement_mutation_without_explicit_begin"] is True
    assert by_name["dynamic_insert"]["dynamic_sql_count"] == 1
    assert by_name["dynamic_insert"]["mutation_kinds"] == ["INSERT"]
    assert by_name["script_migration"]["executescript_count"] == 1
    assert by_name["script_migration"]["mutation_count"] == 2

    rendered = json.dumps(report)
    assert "item_id" not in rendered
    assert "event_id" not in rendered
    assert "CREATE TABLE sample" not in rendered


def test_audit_handles_missing_sources_and_reports_no_false_readiness(tmp_path):
    report = audit_transaction_boundaries(tmp_path)

    assert report["source_files"] == []
    assert report["function_count"] == 0
    assert report["ready_for_postgresql_runtime"] is False
