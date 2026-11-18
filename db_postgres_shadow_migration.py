"""One-command, all-or-nothing SQLite -> PostgreSQL shadow migration.

Creates a no-clobber SQLite backup, restores and verifies that backup,
compares it to the live source, then creates the PostgreSQL schema and copies
all rows in one PostgreSQL transaction. The production app is never switched.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db_backup import backup_database
from db_migration_preflight import build_migration_preflight
from db_postgres_data_migration import (
    _fingerprint,
    _ordered_select,
    _sqlite_autoincrement_high_water,
)
from db_postgres_ddl_plan import build_postgres_ddl_plan
from db_postgres_schema_apply import _source_counts, _target_counts
from db_restore import restore_database
from db_schema_audit import build_postgres_data_preflight, build_schema_manifest


def _copy_all_in_one_transaction(
    source_path: str,
    dsn: str,
    batch_size: int,
    execute: bool,
    target_schema: str | None = None,
) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve(strict=True)
    sqlite = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        integrity = str(sqlite.execute("PRAGMA integrity_check").fetchone()[0])
        fk_violations = len(sqlite.execute("PRAGMA foreign_key_check").fetchall())
        if integrity != "ok" or fk_violations:
            raise RuntimeError(
                f"Migration source is unhealthy: integrity={integrity}, foreign_key_violations={fk_violations}"
            )

        manifest = build_schema_manifest(sqlite)
        boolean_preflight = build_postgres_data_preflight(sqlite, manifest)
        if boolean_preflight["unsafe_column_count"]:
            raise RuntimeError(
                f"PostgreSQL BOOLEAN conversion has {boolean_preflight['unsafe_column_count']} unsafe column(s)."
            )
        ddl_plan = build_postgres_ddl_plan(manifest, boolean_preflight)
        if ddl_plan["blocker_count"]:
            raise RuntimeError(
                f"PostgreSQL DDL plan has {ddl_plan['blocker_count']} blocker(s); refusing shadow migration."
            )

        tables = sorted(manifest["tables"], key=lambda item: str(item["name"]))
        expected_counts = _source_counts(manifest)
        source_profiles = {}
        for table in tables:
            select_sql, _ = _ordered_select(sqlite, table)
            source_profiles[str(table["name"])] = _fingerprint(sqlite.execute(select_sql))

        import psycopg
        from psycopg import sql

        report: dict[str, Any] = {
            "source_schema_sha256": manifest["schema_sha256"],
            "source_counts": expected_counts,
            "boolean_preflight": {
                "column_count": len(boolean_preflight["columns"]),
                "unsafe_column_count": boolean_preflight["unsafe_column_count"],
            },
            "ddl_statement_count": ddl_plan["statement_count"],
            "ddl_blocker_count": ddl_plan["blocker_count"],
            "execute": execute,
            "committed": False,
            "verified": False,
            "tables": [],
            "ready_for_cutover": False,
        }

        with psycopg.connect(dsn, autocommit=False) as pg:
            with pg.cursor() as cursor:
                if target_schema:
                    cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(target_schema)))
                schema = cursor.execute("SELECT current_schema()").fetchone()[0]
                if not schema:
                    raise RuntimeError("PostgreSQL target schema does not exist or is not visible in search_path.")
                existing = cursor.execute(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                       WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','S','i','I'))
                      +
                      (SELECT COUNT(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                       WHERE n.nspname=%s)
                    """,
                    (schema, schema),
                ).fetchone()[0]
                if int(existing):
                    raise RuntimeError("PostgreSQL target schema is not empty; refusing to overwrite existing objects.")

                if not execute:
                    pg.rollback()
                    report["target_schema_empty"] = True
                    return report

                # DDL and all data copies share this single transaction. A failed
                # COPY or verification rolls back both schema and every table.
                for statement in ddl_plan["statements"]:
                    cursor.execute(statement)
                cursor.execute("SET CONSTRAINTS ALL DEFERRED")

                for table in tables:
                    name = str(table["name"])
                    _, columns = _ordered_select(sqlite, table)
                    copy_statement = (
                        f"COPY {sql.Identifier(name).as_string(pg)} "
                        f"({', '.join(sql.Identifier(column).as_string(pg) for column in columns)}) FROM STDIN"
                    )
                    copied = 0
                    with cursor.copy(copy_statement) as copy:
                        source_cursor = sqlite.execute(_ordered_select(sqlite, table)[0])
                        while True:
                            batch = source_cursor.fetchmany(batch_size)
                            if not batch:
                                break
                            for row in batch:
                                copy.write_row(tuple(row))
                            copied += len(batch)
                    expected_count, expected_sha = source_profiles[name]
                    if copied != expected_count:
                        raise RuntimeError(
                            f"Copied row count mismatch for {name}: {copied} != {expected_count}"
                        )
                    report["tables"].append({
                        "table": name,
                        "source_rows": expected_count,
                        "source_sha256": expected_sha,
                        "copied_rows": copied,
                    })

                # Restore SQLite AUTOINCREMENT history, including deleted IDs
                # above MAX(id), so the next PostgreSQL identity never reuses them.
                for table in tables:
                    ddl = str(table.get("sql") or "")
                    if "AUTOINCREMENT" not in ddl.upper():
                        continue
                    primary = sorted(
                        (column for column in table["columns"] if int(column.get("primary_key_position", 0)) > 0),
                        key=lambda column: int(column["primary_key_position"]),
                    )
                    if len(primary) != 1:
                        raise RuntimeError(f"Unexpected AUTOINCREMENT primary key on {table['name']}")
                    column_name = str(primary[0]["name"])
                    sequence = cursor.execute(
                        "SELECT pg_get_serial_sequence(%s, %s)", (str(table["name"]), column_name)
                    ).fetchone()[0]
                    if sequence:
                        max_id = cursor.execute(
                            sql.SQL("SELECT MAX({}) FROM {}").format(
                                sql.Identifier(column_name), sql.Identifier(str(table["name"]))
                            )
                        ).fetchone()[0]
                        high_water = max(_sqlite_autoincrement_high_water(sqlite, str(table["name"])), int(max_id or 0))
                        if high_water > 0:
                            cursor.execute("SELECT setval(%s, %s, true)", (sequence, high_water))
                        else:
                            cursor.execute("SELECT setval(%s, 1, false)", (sequence,))

                for table in tables:
                    name = str(table["name"])
                    _, columns = _ordered_select(sqlite, table)
                    primary = sorted(
                        (column for column in table["columns"] if int(column.get("primary_key_position", 0)) > 0),
                        key=lambda column: int(column["primary_key_position"]),
                    )
                    order = [str(column["name"]) for column in primary] or columns
                    select_target = sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
                        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                        sql.Identifier(name),
                        sql.SQL(", ").join(sql.Identifier(column) for column in order),
                    )
                    target_cursor = cursor.execute(select_target)
                    def target_rows():
                        while True:
                            batch = target_cursor.fetchmany(batch_size)
                            if not batch:
                                break
                            yield from batch
                    target_count, target_sha = _fingerprint(target_rows())
                    expected_count, expected_sha = source_profiles[name]
                    if target_count != expected_count or target_sha != expected_sha:
                        raise RuntimeError(f"PostgreSQL content fingerprint mismatch for {name}")
                    row_report = next(item for item in report["tables"] if item["table"] == name)
                    row_report.update({
                        "target_rows": target_count,
                        "target_sha256": target_sha,
                        "content_verified": True,
                    })

                target_counts = _target_counts(cursor)
                mismatches = {
                    key: {"source": value, "target": target_counts.get(key)}
                    for key, value in expected_counts.items()
                    if target_counts.get(key) != value
                }
                expected_tables = {
                    str(table["name"]): [
                        str(column["name"]) for column in table["columns"]
                        if int(column.get("hidden", 0)) == 0
                    ]
                    for table in tables
                }
                actual_tables = {}
                for (table_name,) in cursor.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY table_name",
                    (schema,),
                ).fetchall():
                    actual_tables[table_name] = [
                        item[0] for item in cursor.execute(
                            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                            (schema, table_name),
                        ).fetchall()
                    ]
                if expected_tables != actual_tables:
                    mismatches["table_columns"] = {"source": expected_tables, "target": actual_tables}
                if mismatches:
                    raise RuntimeError(f"PostgreSQL schema verification mismatch: {mismatches}")

                report["target_counts"] = target_counts
                pg.commit()
                report["committed"] = True
                report["verified"] = True
        return report
    finally:
        sqlite.close()


def run_shadow_migration(
    source_path: str,
    backup_path: str,
    dsn: str,
    execute: bool = False,
    batch_size: int = 1000,
    target_schema: str | None = None,
) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve(strict=True)
    backup = Path(backup_path).expanduser().resolve()
    backup_report = backup_database(source, backup)
    manifest_path = Path(str(backup) + ".manifest.json")

    with tempfile.TemporaryDirectory(prefix="hassle-postgres-restore-") as directory:
        restored_path = Path(directory) / "verified-restore.sqlite3"
        restore_report = restore_database(backup, manifest_path, restored_path)

        source_connection = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30)
        backup_connection = sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True, timeout=30)
        try:
            # Hold a consistent read snapshot while comparing the live database
            # to the verified backup. A concurrent write must not create a
            # mixed-time preflight report.
            source_connection.execute("BEGIN")
            backup_connection.execute("BEGIN")
            preflight = build_migration_preflight(
                source_connection,
                backup_connection,
                repository_root=Path(__file__).resolve().parent,
            )
        finally:
            source_connection.close()
            backup_connection.close()

        if not preflight["comparison"]["exact_match"]:
            raise RuntimeError("SQLite source and verified backup differ; refusing PostgreSQL shadow migration.")
        if not preflight["postgresql_boolean_data_preflight"]["conversion_safe_in_both"]:
            raise RuntimeError("SQLite BOOLEAN candidate data is not safe for PostgreSQL conversion.")

        migration_report = _copy_all_in_one_transaction(
            str(backup), dsn, batch_size, execute, target_schema
        )
        return {
            "backup": {
                "file": backup.name,
                "manifest_file": manifest_path.name,
                "sha256": backup_report["backup_sha256"],
                "table_count": backup_report["table_count"],
                "total_rows": backup_report["total_rows"],
            },
            "restore_verified": bool(restore_report["restored"]),
            "preflight_exact_match": bool(preflight["comparison"]["exact_match"]),
            "migration": migration_report,
            "production_app_switched": False,
            "cutover_allowed": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="SQLite source path; defaults to the configured marketplace database")
    parser.add_argument("--backup", help="Unique no-clobber SQLite backup destination")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--target-schema", help="Existing empty PostgreSQL schema; defaults to current_schema")
    parser.add_argument("--execute", action="store_true", help="Create schema and copy data; default is dry-run")
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 10000:
        parser.error("--batch-size must be between 1 and 10000")
    dsn = os.getenv("HASSLE_POSTGRES_MIGRATION_DSN")
    if not dsn:
        parser.error("Set HASSLE_POSTGRES_MIGRATION_DSN; its value is never printed.")
    source = args.source
    if not source:
        from marketplace import DB_PATH
        source = DB_PATH
    backup = args.backup
    if not backup:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = str(Path(source).expanduser().resolve().parent / f"hassle-postgres-shadow-{stamp}.sqlite3")
    result = run_shadow_migration(source, backup, dsn, args.execute, args.batch_size, args.target_schema)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



def startup_migration_requested(value: str | None = None) -> bool:
    """Require the explicit 'execute' value; all other values are inert."""
    if value is None:
        value = os.getenv("HASSLE_POSTGRES_SHADOW_MIGRATION", "")
    return value.strip().lower() == "execute"
