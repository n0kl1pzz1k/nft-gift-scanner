import json
import sqlite3
from pathlib import Path

from db_backup import backup_database
from db_migration_preflight import build_migration_preflight, _write_report_no_clobber


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



def test_preflight_report_is_created_without_overwriting_existing_file(tmp_path):
    report_path = tmp_path / "reports" / "preflight.json"
    _write_report_no_clobber(report_path, '{"safe": true}\n')
    assert report_path.read_text(encoding="utf-8") == '{"safe": true}\n'

    import pytest

    with pytest.raises(FileExistsError):
        _write_report_no_clobber(report_path, '{"safe": false}\n')

    assert report_path.read_text(encoding="utf-8") == '{"safe": true}\n'


def test_preflight_report_does_not_overwrite_racing_destination(tmp_path, monkeypatch):
    import pytest
    import db_migration_preflight

    report_path = tmp_path / "preflight.json"

    def create_racing_file(source, destination):
        Path(destination).write_text("created-by-another-process", encoding="utf-8")
        raise FileExistsError("destination appeared during publication")

    monkeypatch.setattr(db_migration_preflight.os, "link", create_racing_file)
    with pytest.raises(FileExistsError):
        _write_report_no_clobber(report_path, '{"report": true}\n')

    assert report_path.read_text(encoding="utf-8") == "created-by-another-process"
    assert not list(tmp_path.glob(".preflight.json.*.tmp"))


def test_preflight_profiles_boolean_candidates_for_source_and_backup(tmp_path):
    source_path = tmp_path / "source_boolean.sqlite3"
    backup_path = tmp_path / "backup_boolean.sqlite3"
    connection = sqlite3.connect(source_path)
    connection.executescript(
        """
        CREATE TABLE telegram_users (
            id INTEGER PRIMARY KEY,
            is_premium INTEGER
        );
        INSERT INTO telegram_users VALUES (1, 0);
        INSERT INTO telegram_users VALUES (2, 1);
        INSERT INTO telegram_users VALUES (3, NULL);
        """
    )
    connection.commit()
    connection.close()
    backup_database(source_path, backup_path)
    source, backup = _read_only(source_path), _read_only(backup_path)
    try:
        report = build_migration_preflight(source, backup, tmp_path)
    finally:
        source.close()
        backup.close()

    profile = report["postgresql_boolean_data_preflight"]
    assert profile["profiles_match"] is True
    assert profile["conversion_safe_in_both"] is True
    assert profile["source"]["columns"][0]["nullable"] is True
    assert profile["source"]["columns"][0]["null_value_count"] == 1
    assert report["postgresql_cutover_allowed"] is False


def test_preflight_marks_non_boolean_values_as_conversion_blocker(tmp_path):
    source_path = tmp_path / "source_invalid_boolean.sqlite3"
    backup_path = tmp_path / "backup_invalid_boolean.sqlite3"
    connection = sqlite3.connect(source_path)
    connection.executescript(
        """
        CREATE TABLE telegram_users (
            id INTEGER PRIMARY KEY,
            is_premium INTEGER
        );
        INSERT INTO telegram_users VALUES (1, 0);
        INSERT INTO telegram_users VALUES (2, 7);
        """
    )
    connection.commit()
    connection.close()
    backup_database(source_path, backup_path)
    source, backup = _read_only(source_path), _read_only(backup_path)
    try:
        report = build_migration_preflight(source, backup, tmp_path)
    finally:
        source.close()
        backup.close()

    profile = report["postgresql_boolean_data_preflight"]
    assert profile["profiles_match"] is True
    assert profile["conversion_safe_in_both"] is False
    assert profile["source"]["columns"][0]["invalid_value_count"] == 1
    assert report["postgresql_cutover_allowed"] is False



def test_preflight_detects_non_integer_storage_in_bigint_candidates(tmp_path):
    source_path = tmp_path / "numeric-source.sqlite3"
    backup_path = tmp_path / "numeric-backup.sqlite3"
    for path in (source_path, backup_path):
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE sample (id INTEGER PRIMARY KEY, event_time INTEGER NOT NULL);
            INSERT INTO sample(event_time) VALUES (1700000000);
            INSERT INTO sample(event_time) VALUES ('not-a-timestamp');
            """
        )
        connection.commit()
        connection.close()

    source = sqlite3.connect(source_path)
    backup = sqlite3.connect(backup_path)
    report = build_migration_preflight(source, backup)
    source.close()
    backup.close()

    numeric = report["postgresql_integer_data_preflight"]
    assert numeric["conversion_safe_in_both"] is False
    assert numeric["unsafe_column_count"] == 2
    assert next(item for item in numeric["source"]["integer_columns"] if item["column"] == "event_time")["text_storage_count"] == 1
    assert report["ready_for_shadow_migration"] is False



def test_preflight_requires_safe_double_precision_storage_classes(tmp_path):
    source_path = tmp_path / "real-source.sqlite3"
    backup_path = tmp_path / "real-backup.sqlite3"
    for path in (source_path, backup_path):
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE catalogue_gift_snapshots (observed_at INTEGER NOT NULL);
            INSERT INTO catalogue_gift_snapshots VALUES (1700000000.125);
            """
        )
        connection.commit()
        connection.close()

    source = sqlite3.connect(source_path)
    backup = sqlite3.connect(backup_path)
    report = build_migration_preflight(source, backup)
    source.close()
    backup.close()

    assert report["postgresql_real_data_preflight"]["conversion_safe_in_both"] is True
    assert report["ready_for_shadow_migration"] is True
