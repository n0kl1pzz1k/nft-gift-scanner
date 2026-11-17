import json
import sqlite3

from db_backup import backup_database
from db_migration_preflight import build_migration_preflight


def _create_source(path):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT NOT NULL UNIQUE
        );
        CREATE TABLE listings (
            id INTEGER PRIMARY KEY,
            owner_id INTEGER NOT NULL REFERENCES users(id),
            title TEXT NOT NULL
        );
        INSERT INTO users(telegram_id) VALUES ('private-user-id');
        INSERT INTO listings(id, owner_id, title) VALUES (41, 1, 'PRIVATE_GIFT_TITLE');
        """
    )
    connection.commit()
    connection.close()


def _read_only(path):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    connection.execute("BEGIN")
    return connection


def test_preflight_confirms_exact_backup_without_exporting_rows(tmp_path):
    source_path = tmp_path / "source.sqlite3"
    backup_path = tmp_path / "backup.sqlite3"
    _create_source(source_path)
    backup_database(source_path, backup_path)
    source, backup = _read_only(source_path), _read_only(backup_path)
    try:
        report = build_migration_preflight(source, backup, tmp_path)
    finally:
        source.close()
        backup.close()
    assert report["comparison"]["exact_match"] is True
    assert report["ready_for_shadow_migration"] is True
    assert report["postgresql_cutover_allowed"] is False
    assert report["source"]["tables"] == {"listings": 1, "users": 1}
    assert report["comparison"]["sequence_counters_match"] is True
    rendered = json.dumps(report, ensure_ascii=False)
    assert "private-user-id" not in rendered
    assert "PRIVATE_GIFT_TITLE" not in rendered


def test_preflight_detects_changed_row_even_when_count_matches(tmp_path):
    source_path = tmp_path / "source.sqlite3"
    backup_path = tmp_path / "backup.sqlite3"
    _create_source(source_path)
    backup_database(source_path, backup_path)
    changed = sqlite3.connect(backup_path)
    changed.execute("UPDATE listings SET title = 'changed' WHERE id = 41")
    changed.commit()
    changed.close()
    source, backup = _read_only(source_path), _read_only(backup_path)
    try:
        report = build_migration_preflight(source, backup, tmp_path)
    finally:
        source.close()
        backup.close()
    assert report["comparison"]["row_counts_match"] is True
    assert report["comparison"]["row_hashes_match"] is False
    assert report["comparison"]["exact_match"] is False
    assert report["ready_for_shadow_migration"] is False


def test_preflight_detects_schema_and_sequence_drift(tmp_path):
    source_path = tmp_path / "source.sqlite3"
    backup_path = tmp_path / "backup.sqlite3"
    _create_source(source_path)
    backup_database(source_path, backup_path)
    changed = sqlite3.connect(backup_path)
    changed.execute("CREATE TABLE extra_table (id TEXT PRIMARY KEY)")
    changed.execute("DELETE FROM users WHERE id = 1")
    changed.execute("INSERT INTO users(id, telegram_id) VALUES (9, 'replacement')")
    changed.commit()
    changed.close()
    source, backup = _read_only(source_path), _read_only(backup_path)
    try:
        report = build_migration_preflight(source, backup, tmp_path)
    finally:
        source.close()
        backup.close()
    assert report["comparison"]["schema_matches"] is False
    assert report["comparison"]["sequence_counters_match"] is False
    assert report["ready_for_shadow_migration"] is False
