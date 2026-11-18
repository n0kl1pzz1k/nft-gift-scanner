"""One-command, all-or-nothing SQLite -> PostgreSQL shadow migration.

Creates a no-clobber SQLite snapshot, restores and verifies that snapshot,
compares the immutable backup with its restore, then creates the PostgreSQL
schema and copies all rows in one transaction. The production app is never switched.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
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
from db_postgres_migration_plan import build_postgres_migration_plan
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
            "integer_preflight": {
                "column_count": len(boolean_preflight["integer_columns"]),
                "unsafe_column_count": boolean_preflight["unsafe_integer_column_count"],
                "real_backed_integer_column_count": boolean_preflight["real_backed_integer_column_count"],
                "type_override_count": boolean_preflight["integer_type_override_count"],
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
                    cursor.execute(
                        sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(target_schema))
                    )
                    cursor.execute(
                        sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(target_schema))
                    )
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

                # User triggers can create side effects during COPY (for
                # example, historical offers would generate duplicate
                # notifications). Disable only user triggers; internal FK
                # constraint triggers remain active.
                for table in tables:
                    cursor.execute(
                        sql.SQL("ALTER TABLE {} DISABLE TRIGGER USER").format(
                            sql.Identifier(str(table["name"]))
                        )
                    )

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

                # Force deferred FK checks now; PostgreSQL will not allow
                # ALTER TABLE while deferred constraint trigger events remain.
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

                # Re-enable business triggers only after every historical row
                # has been copied. They will process future application writes.
                for table in tables:
                    cursor.execute(
                        sql.SQL("ALTER TABLE {} ENABLE TRIGGER USER").format(
                            sql.Identifier(str(table["name"]))
                        )
                    )

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
                    if not sequence:
                        raise RuntimeError(
                            f"PostgreSQL AUTOINCREMENT column {table['name']}.{column_name} has no owned identity sequence."
                        )
                    max_id = cursor.execute(
                        sql.SQL("SELECT MAX({}) FROM {}").format(
                            sql.Identifier(column_name), sql.Identifier(str(table["name"]))
                        )
                    ).fetchone()[0]
                    high_water = max(_sqlite_autoincrement_high_water(sqlite, str(table["name"])), int(max_id or 0))
                    if high_water > 0:
                        sequence_value = cursor.execute(
                            "SELECT setval(%s, %s, true)", (sequence, high_water)
                        ).fetchone()[0]
                        expected_next = high_water + 1
                    else:
                        sequence_value = cursor.execute(
                            "SELECT setval(%s, 1, false)", (sequence,)
                        ).fetchone()[0]
                        expected_next = 1
                    if int(sequence_value) != max(high_water, 1):
                        raise RuntimeError(
                            f"PostgreSQL identity sequence for {table['name']} was not restored to its expected high-water mark."
                        )
                    report.setdefault("identity_sequences", []).append({
                        "table": str(table["name"]),
                        "column": column_name,
                        "high_water": high_water,
                        "next_generated_id": expected_next,
                        "restored": True,
                    })

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

                # Verify non-row schema objects too. Matching row fingerprints
                # alone cannot detect a missing index, FK, CHECK, or trigger.
                structural = build_postgres_migration_plan(manifest)
                expected_indexes = {
                    str(index["name"])
                    for table in tables
                    for index in table.get("indexes", [])
                    if index.get("sql")
                }
                actual_indexes = {
                    item[0] for item in cursor.execute(
                        "SELECT indexname FROM pg_indexes WHERE schemaname=%s", (schema,)
                    ).fetchall()
                }
                missing_indexes = sorted(expected_indexes - actual_indexes)
                if missing_indexes:
                    mismatches["indexes"] = {"missing": missing_indexes}

                expected_triggers = {
                    str(item["name"]) for item in manifest.get("objects", [])
                    if item.get("type") == "trigger"
                }
                actual_triggers = {
                    item[0] for item in cursor.execute(
                        """SELECT t.tgname
                           FROM pg_trigger t
                           JOIN pg_class c ON c.oid=t.tgrelid
                           JOIN pg_namespace n ON n.oid=c.relnamespace
                           WHERE n.nspname=%s AND NOT t.tgisinternal""",
                        (schema,),
                    ).fetchall()
                }
                missing_triggers = sorted(expected_triggers - actual_triggers)
                if missing_triggers:
                    mismatches["triggers"] = {"missing": missing_triggers}

                expected_foreign_key_signatures = sorted(
                    (
                        str(table["table"]),
                        str(fk["referenced_table"]),
                        tuple(str(column["from"]) for column in fk["columns"]),
                        tuple(str(column["to"]) for column in fk["columns"]),
                        str(fk["on_delete"]).upper(),
                        str(fk["on_update"]).upper(),
                    )
                    for table in structural["tables"]
                    for fk in table.get("foreign_keys", [])
                )
                expected_foreign_keys = len(expected_foreign_key_signatures)
                actual_foreign_key_rows = cursor.execute(
                    """SELECT c.relname, referenced.relname,
                              ARRAY(
                                SELECT a.attname
                                FROM unnest(con.conkey) WITH ORDINALITY AS key(attnum, ord)
                                JOIN pg_attribute a
                                  ON a.attrelid=c.oid AND a.attnum=key.attnum
                                ORDER BY key.ord
                              ),
                              ARRAY(
                                SELECT a.attname
                                FROM unnest(con.confkey) WITH ORDINALITY AS key(attnum, ord)
                                JOIN pg_attribute a
                                  ON a.attrelid=referenced.oid AND a.attnum=key.attnum
                                ORDER BY key.ord
                              ),
                              con.confdeltype, con.confupdtype,
                              con.condeferrable, con.condeferred
                       FROM pg_constraint con
                       JOIN pg_class c ON c.oid=con.conrelid
                       JOIN pg_namespace n ON n.oid=c.relnamespace
                       JOIN pg_class referenced ON referenced.oid=con.confrelid
                       JOIN pg_namespace referenced_ns ON referenced_ns.oid=referenced.relnamespace
                       WHERE n.nspname=%s AND referenced_ns.nspname=n.nspname
                         AND con.contype='f'""",
                    (schema,),
                ).fetchall()
                action_names = {
                    "a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE",
                    "n": "SET NULL", "d": "SET DEFAULT",
                }
                actual_foreign_key_signatures = sorted(
                    (
                        str(row[0]), str(row[1]), tuple(row[2]), tuple(row[3]),
                        action_names.get(str(row[4]), f"UNKNOWN:{row[4]}"),
                        action_names.get(str(row[5]), f"UNKNOWN:{row[5]}"),
                    )
                    for row in actual_foreign_key_rows
                )
                actual_foreign_keys = len(actual_foreign_key_signatures)
                if actual_foreign_key_signatures != expected_foreign_key_signatures:
                    mismatches["foreign_keys"] = {
                        "source": expected_foreign_key_signatures,
                        "target": actual_foreign_key_signatures,
                    }
                non_deferred_foreign_keys = [
                    {"table": row[0], "referenced_table": row[1]}
                    for row in actual_foreign_key_rows
                    if not row[6] or not row[7]
                ]
                if non_deferred_foreign_keys:
                    mismatches["foreign_key_deferral"] = {
                        "expected": "all constraints DEFERRABLE INITIALLY DEFERRED",
                        "non_deferred": non_deferred_foreign_keys,
                    }

                expected_checks = sum(
                    len(item.get("check_constraints", [])) for item in structural["tables"]
                )
                actual_checks = int(cursor.execute(
                    """SELECT COUNT(*) FROM pg_constraint con
                       JOIN pg_class c ON c.oid=con.conrelid
                       JOIN pg_namespace n ON n.oid=c.relnamespace
                       WHERE n.nspname=%s AND con.contype='c'""",
                    (schema,),
                ).fetchone()[0])
                if actual_checks != expected_checks:
                    mismatches["check_constraints"] = {
                        "source": expected_checks, "target": actual_checks
                    }

                report["schema_objects"] = {
                    "indexes_expected": len(expected_indexes),
                    "indexes_found": len(expected_indexes) - len(missing_indexes),
                    "triggers_expected": len(expected_triggers),
                    "triggers_found": len(expected_triggers) - len(missing_triggers),
                    "foreign_keys_expected": expected_foreign_keys,
                    "foreign_keys_found": actual_foreign_keys,
                    "foreign_keys_semantically_verified": actual_foreign_key_signatures == expected_foreign_key_signatures and not non_deferred_foreign_keys,
                    "check_constraints_expected": expected_checks,
                    "check_constraints_found": actual_checks,
                    "verified": not any(key in mismatches for key in (
                        "indexes", "triggers", "foreign_keys", "foreign_key_deferral", "check_constraints"
                    )),
                }
                if mismatches:
                    raise RuntimeError(f"PostgreSQL schema verification mismatch: {mismatches}")

                report["target_counts"] = target_counts
                pg.commit()
                report["committed"] = True
                report["verified"] = True
        return report
    finally:
        sqlite.close()



def _disk_space_preflight(source: Path, backup: Path) -> dict[str, Any]:
    """Ensure the backup and temporary restore fit before writing to the SQLite volume."""
    if not source.is_file():
        raise ValueError("SQLite source database does not exist.")
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30) as connection:
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    snapshot_bytes = max(source.stat().st_size, page_count * page_size)
    reserve_bytes = 32 * 1024 * 1024
    backup_usage = shutil.disk_usage(backup.parent)
    restore_directory = Path(tempfile.gettempdir())
    restore_usage = shutil.disk_usage(restore_directory)
    same_filesystem = os.stat(backup.parent).st_dev == os.stat(restore_directory).st_dev

    if same_filesystem:
        required_bytes = snapshot_bytes * 2 + reserve_bytes
        if backup_usage.free < required_bytes:
            raise RuntimeError("Insufficient free disk space for the verified SQLite backup and restore.")
    else:
        required_bytes = snapshot_bytes + reserve_bytes
        if backup_usage.free < required_bytes or restore_usage.free < required_bytes:
            raise RuntimeError("Insufficient free disk space for the verified SQLite backup or restore.")

    return {
        "snapshot_bytes": snapshot_bytes,
        "backup_free_bytes": backup_usage.free,
        "restore_free_bytes": restore_usage.free,
        "required_bytes_per_filesystem": required_bytes,
        "same_filesystem": same_filesystem,
    }


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
    # Refuse before creating any snapshot artifacts if the backup and restore
    # cannot fit on their respective filesystems.
    disk_preflight = _disk_space_preflight(source, backup)
    backup_report = backup_database(source, backup)
    manifest_path = Path(str(backup) + ".manifest.json")

    with tempfile.TemporaryDirectory(prefix="hassle-postgres-restore-") as directory:
        restored_path = Path(directory) / "verified-restore.sqlite3"
        restore_report = restore_database(backup, manifest_path, restored_path)

        snapshot_connection = sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True, timeout=30)
        restored_connection = sqlite3.connect(restored_path.as_uri() + "?mode=ro", uri=True, timeout=30)
        try:
            # Compare the immutable snapshot with its verified restore, not the
            # live database. The app can keep serving SQLite while this shadow
            # copy runs; writes after the snapshot remain safely on SQLite.
            snapshot_connection.execute("BEGIN")
            restored_connection.execute("BEGIN")
            preflight = build_migration_preflight(
                snapshot_connection,
                restored_connection,
                repository_root=Path(__file__).resolve().parent,
            )
        finally:
            snapshot_connection.close()
            restored_connection.close()

        if not preflight["comparison"]["exact_match"]:
            raise RuntimeError("Verified SQLite backup and restored copy differ; refusing PostgreSQL shadow migration.")
        if not preflight["postgresql_boolean_data_preflight"]["conversion_safe_in_both"]:
            raise RuntimeError("SQLite BOOLEAN candidate data is not safe for PostgreSQL conversion.")
        if not preflight["postgresql_integer_data_preflight"]["conversion_safe_in_both"]:
            raise RuntimeError("SQLite INTEGER-affinity columns contain non-integer storage classes; refusing BIGINT conversion.")
        if not preflight["postgresql_real_data_preflight"]["conversion_safe_in_both"]:
            raise RuntimeError("SQLite DOUBLE PRECISION candidates contain text/blob storage classes; refusing numeric conversion.")

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
            "disk_preflight": disk_preflight,
            "preflight_exact_match": bool(preflight["comparison"]["exact_match"]),
            "target_schema": target_schema,
            "migration": migration_report,
            "production_app_switched": False,
            "cutover_allowed": False,
        }



def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _shadow_migration_error_code(error: BaseException) -> str:
    """Map migration failures to safe diagnostic codes without persisting secrets or row values."""
    message = str(error).lower()
    known = (
        ("insufficient free disk space", "insufficient_disk_space"),
        ("migration source is unhealthy", "source_integrity_failed"),
        ("boolean conversion", "unsafe_boolean_data"),
        ("ddl plan has", "ddl_blockers_present"),
        ("target schema is not empty", "target_schema_not_empty"),
        ("target schema does not exist", "target_schema_not_visible"),
        ("copied row count mismatch", "row_count_mismatch"),
        ("unexpected autoincrement primary key", "identity_definition_mismatch"),
        ("has no owned identity sequence", "identity_sequence_missing"),
        ("identity sequence", "identity_sequence_mismatch"),
        ("content fingerprint mismatch", "content_fingerprint_mismatch"),
        ("schema verification mismatch", "schema_verification_mismatch"),
        ("backup and restored copy differ", "backup_restore_mismatch"),
        ("boolean candidate data", "unsafe_boolean_data"),
        ("integer-affinity columns contain", "unsafe_integer_data"),
        ("double precision candidates contain", "unsafe_real_data"),
    )
    for fragment, code in known:
        if fragment in message:
            return code
    return "unclassified_" + type(error).__name__.lower()


def run_shadow_migration_once(
    source_path: str,
    backup_path: str,
    dsn: str,
    marker_path: str,
    run_id: str,
    target_schema: str,
    batch_size: int = 1000,
) -> dict[str, Any]:
    """Run one opt-in shadow migration and persist a durable no-retry marker."""
    if not run_id or len(run_id) > 48 or not all(char.isalnum() or char in "_-" for char in run_id):
        raise ValueError("Shadow migration run ID must be 1-48 URL-safe characters.")
    if not target_schema or len(target_schema) > 48 or not all(char.isalnum() or char == "_" for char in target_schema):
        raise ValueError("Shadow migration schema must be 1-48 alphanumeric/underscore characters.")
    if batch_size < 1 or batch_size > 10000:
        raise ValueError("Shadow migration batch size must be between 1 and 10000.")

    marker = Path(marker_path).expanduser().resolve()
    marker.parent.mkdir(parents=True, exist_ok=True)
    initial = {
        "run_id": run_id,
        "target_schema": target_schema,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
            previous_status = str(previous.get("status", "unknown"))
        except Exception:
            previous_status = "unknown"
        return {
            "status": "skipped",
            "reason": "marker_exists",
            "run_id": run_id,
            "previous_status": previous_status,
        }

    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(initial, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\\n")
        stream.flush()
        os.fsync(stream.fileno())

    try:
        result = run_shadow_migration(
            source_path, backup_path, dsn, execute=True,
            batch_size=batch_size, target_schema=target_schema,
        )
        migration = result.get("migration", {})
        if not migration.get("committed") or not migration.get("verified"):
            raise RuntimeError("Shadow migration did not reach verified commit state.")
        summary = {
            "run_id": run_id,
            "target_schema": target_schema,
            "status": "verified",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "backup_sha256": result["backup"]["sha256"],
            "disk_preflight": result.get("disk_preflight"),
            "table_count": result["backup"]["table_count"],
            "total_rows": result["backup"]["total_rows"],
            "schema_sha256": migration.get("source_schema_sha256"),
            "integer_preflight": migration.get("integer_preflight"),
            "ddl_statement_count": migration.get("ddl_statement_count"),
            "target_counts": migration.get("target_counts"),
            "production_app_switched": False,
            "cutover_allowed": False,
        }
        _write_json_atomic(marker, summary)
        return summary
    except Exception as exc:
        failure = {
            "run_id": run_id,
            "target_schema": target_schema,
            "status": "failed",
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "error_type": type(exc).__name__,
            "error_code": _shadow_migration_error_code(exc),
            "production_app_switched": False,
        }
        _write_json_atomic(marker, failure)
        return failure

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
