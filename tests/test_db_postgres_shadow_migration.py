import sqlite3

import psycopg
import pytest
from psycopg import sql

from db_postgres_shadow_migration import run_shadow_migration


def test_shadow_migration_creates_schema_and_copies_all_rows_atomically(tmp_path):
    import os
    import uuid

    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    source_path = tmp_path / "source.sqlite3"
    source = sqlite3.connect(source_path)
    source.execute("CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT NOT NULL)")
    source.execute("INSERT INTO items(value) VALUES ('first')")
    source.execute("INSERT INTO items(value) VALUES ('deleted-high-water')")
    source.execute("DELETE FROM items WHERE id = 2")
    source.commit()
    source.close()

    backup_path = tmp_path / "shadow-backup.sqlite3"
    schema = "mig_shadow_" + uuid.uuid4().hex[:12]
    with psycopg.connect(dsn, autocommit=True) as pg:
        try:
            dry = run_shadow_migration(str(source_path), str(backup_path), dsn, execute=False, target_schema=schema)
            assert dry["restore_verified"] is True
            assert dry["preflight_exact_match"] is True
            assert dry["migration"]["committed"] is False
            assert dry["migration"]["target_schema_empty"] is True
            assert pg.execute(
                "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=%s", (schema,)
            ).fetchone()[0] == 0

            report = run_shadow_migration(
                str(source_path), str(tmp_path / "shadow-backup-execute.sqlite3"),
                dsn, execute=True, target_schema=schema,
            )
            assert report["migration"]["committed"] is True
            assert report["migration"]["verified"] is True
            assert report["migration"]["tables"][0]["content_verified"] is True
            assert report["production_app_switched"] is False
            pg.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            rows = pg.execute("SELECT id, value FROM items").fetchall()
            next_id = pg.execute("SELECT nextval(pg_get_serial_sequence('items', 'id'))").fetchone()[0]
            assert rows == [(1, "first")]
            assert next_id == 3
        finally:
            pg.execute("SET search_path TO public")
            pg.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def test_shadow_migration_rolls_back_schema_and_rows_on_copy_failure(tmp_path):
    import os
    import uuid

    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    source_path = tmp_path / "bad-source.sqlite3"
    source = sqlite3.connect(source_path)
    source.executescript(
        """
        CREATE TABLE a_items (id TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE b_items (id TEXT PRIMARY KEY, value INTEGER NOT NULL);
        INSERT INTO a_items VALUES ('a1', 'valid');
        INSERT INTO b_items VALUES ('b1', 'not-an-integer');
        """
    )
    source.commit()
    source.close()

    schema = "mig_shadow_rollback_" + uuid.uuid4().hex[:10]
    with psycopg.connect(dsn, autocommit=True) as pg:
        try:
            with pytest.raises(psycopg.Error):
                run_shadow_migration(
                    str(source_path), str(tmp_path / "bad-backup.sqlite3"),
                    dsn, execute=True, target_schema=schema,
                )
            remaining_schema = pg.execute(
                "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=%s", (schema,)
            ).fetchone()[0]
            assert remaining_schema == 0
        finally:
            pg.execute("SET search_path TO public")
            pg.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def test_shadow_migration_dry_run_does_not_persist_new_target_schema(tmp_path):
    import os
    import uuid

    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    source_path = tmp_path / "dry-schema-source.sqlite3"
    source = sqlite3.connect(source_path)
    source.execute("CREATE TABLE items (id TEXT PRIMARY KEY, value TEXT)")
    source.execute("INSERT INTO items VALUES ('one', 'value')")
    source.commit()
    source.close()

    schema = "dry_shadow_" + uuid.uuid4().hex[:10]
    report = run_shadow_migration(
        str(source_path),
        str(tmp_path / "dry-schema-backup.sqlite3"),
        dsn,
        execute=False,
        target_schema=schema,
    )
    assert report["migration"]["target_schema_empty"] is True
    with psycopg.connect(dsn) as pg:
        assert pg.execute(
            "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=%s", (schema,)
        ).fetchone()[0] == 0


def test_shadow_migration_once_writes_marker_and_never_retries(tmp_path, monkeypatch):
    import db_postgres_shadow_migration as migration

    calls = []
    def fake_run(source_path, backup_path, dsn, execute, batch_size, target_schema):
        calls.append((source_path, backup_path, execute, target_schema))
        return {
            "backup": {"sha256": "a" * 64, "table_count": 1, "total_rows": 2},
            "migration": {
                "committed": True,
                "verified": True,
                "source_schema_sha256": "b" * 64,
                "ddl_statement_count": 3,
                "target_counts": {"tables": 1, "rows": 2},
            },
            "production_app_switched": False,
            "cutover_allowed": False,
        }
    monkeypatch.setattr(migration, "run_shadow_migration", fake_run)
    marker = tmp_path / ".shadow-run.json"
    result = migration.run_shadow_migration_once(
        str(tmp_path / "source.sqlite3"),
        str(tmp_path / "backup.sqlite3"),
        "postgresql://private-secret",
        str(marker),
        "test-run-01",
        "hassle_shadow_test",
    )
    assert result["status"] == "verified"
    assert result["production_app_switched"] is False
    assert result["cutover_allowed"] is False
    assert "private-secret" not in marker.read_text(encoding="utf-8")

    skipped = migration.run_shadow_migration_once(
        str(tmp_path / "source.sqlite3"),
        str(tmp_path / "backup.sqlite3"),
        "postgresql://private-secret",
        str(marker),
        "test-run-01",
        "hassle_shadow_test",
    )
    assert skipped["status"] == "skipped"
    assert len(calls) == 1


def test_shadow_migration_once_records_failure_without_leaking_details(tmp_path, monkeypatch):
    import db_postgres_shadow_migration as migration

    def fail_run(*args, **kwargs):
        raise RuntimeError("private connection details must not be persisted")
    monkeypatch.setattr(migration, "run_shadow_migration", fail_run)
    marker = tmp_path / ".shadow-failed.json"
    result = migration.run_shadow_migration_once(
        str(tmp_path / "source.sqlite3"),
        str(tmp_path / "backup.sqlite3"),
        "postgresql://private-secret",
        str(marker),
        "test-run-fail",
        "hassle_shadow_fail",
    )
    assert result["status"] == "failed"
    assert result["error_type"] == "RuntimeError"
    assert "private connection details" not in marker.read_text(encoding="utf-8")
    assert "private-secret" not in marker.read_text(encoding="utf-8")


def test_shadow_migration_checks_backup_and_restore_disk_space_before_writing(tmp_path, monkeypatch):
    import shutil
    from types import SimpleNamespace
    from db_postgres_shadow_migration import _disk_space_preflight

    source_path = tmp_path / "disk-check.sqlite3"
    source = sqlite3.connect(source_path)
    source.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)")
    source.execute("INSERT INTO items VALUES (1, 'value')")
    source.commit()
    source.close()

    monkeypatch.setattr(shutil, "disk_usage", lambda path: SimpleNamespace(free=0, total=0, used=0))
    with pytest.raises(RuntimeError, match="Insufficient free disk space"):
        _disk_space_preflight(source_path, tmp_path / "disk-check-backup.sqlite3")
