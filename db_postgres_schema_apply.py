"""Apply a verified PostgreSQL schema plan to an empty target schema.

The command is dry-run by default. Applying requires --apply, a read-only
SQLite source, a PostgreSQL DSN supplied through the environment, and an
empty target schema. All DDL executes in one PostgreSQL transaction.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from db_postgres_ddl_plan import build_postgres_ddl_plan
from db_schema_audit import (
    _extract_check_constraints,
    build_postgres_data_preflight,
    build_postgres_numeric_data_preflight,
    build_schema_manifest,
)


def _source_counts(manifest: dict[str, Any]) -> dict[str, int]:
    return {
        "tables": len(manifest.get("tables", [])),
        "columns": sum(len(table.get("columns", [])) for table in manifest.get("tables", [])),
        "foreign_keys": sum(
            len({int(item.get("id", 0)) for item in table.get("foreign_keys", [])})
            for table in manifest.get("tables", [])
        ),
        "check_constraints": sum(
            len(_extract_check_constraints(str(table.get("sql") or "")))
            for table in manifest.get("tables", [])
        ),
        "indexes": sum(len(table.get("indexes", [])) for table in manifest.get("tables", [])) + sum(
            1 for table in manifest.get("tables", [])
            if "WITHOUT ROWID" not in str(table.get("sql") or "").upper()
            and len([column for column in table.get("columns", []) if int(column.get("primary_key_position", 0)) > 0]) == 1
            and next(column for column in table.get("columns", []) if int(column.get("primary_key_position", 0)) > 0).get("type", "").strip().upper() == "INTEGER"
            and not any(index.get("origin") == "pk" for index in table.get("indexes", []))
        ),
        "triggers": sum(item.get("type") == "trigger" for item in manifest.get("objects", [])),
        "views": sum(item.get("type") == "view" for item in manifest.get("objects", [])),
    }


def _target_counts(cursor: Any) -> dict[str, int]:
    schema = cursor.execute("SELECT current_schema()").fetchone()[0]
    return {
        "tables": cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'",
            (schema,),
        ).fetchone()[0],
        "columns": cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=%s",
            (schema,),
        ).fetchone()[0],
        "foreign_keys": cursor.execute(
            "SELECT COUNT(*) FROM information_schema.table_constraints WHERE constraint_schema=%s AND constraint_type='FOREIGN KEY'",
            (schema,),
        ).fetchone()[0],
        "check_constraints": cursor.execute(
            "SELECT COUNT(*) FROM pg_constraint con JOIN pg_class rel ON rel.oid=con.conrelid JOIN pg_namespace ns ON ns.oid=rel.relnamespace WHERE ns.nspname=%s AND con.contype='c'",
            (schema,),
        ).fetchone()[0],
        "indexes": cursor.execute(
            "SELECT COUNT(*) FROM pg_indexes WHERE schemaname=%s",
            (schema,),
        ).fetchone()[0],
        "triggers": cursor.execute(
            "SELECT COUNT(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND NOT t.tgisinternal",
            (schema,),
        ).fetchone()[0],
        "views": cursor.execute(
            "SELECT COUNT(*) FROM information_schema.views WHERE table_schema=%s",
            (schema,),
        ).fetchone()[0],
    }


def apply_schema(source_path: str, dsn: str, apply: bool = False, target_schema: str | None = None) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve(strict=True)
    uri = f"file:{source.as_posix()}?mode=ro"
    sqlite = sqlite3.connect(uri, uri=True)
    try:
        integrity = str(sqlite.execute("PRAGMA integrity_check").fetchone()[0])
        fk_errors = int(sqlite.execute("SELECT COUNT(*) FROM pragma_foreign_key_check").fetchone()[0])
        if integrity != "ok" or fk_errors:
            raise RuntimeError(f"SQLite source is not healthy: integrity={integrity}, foreign_key_errors={fk_errors}")
        manifest = build_schema_manifest(sqlite)
        data_preflight = build_postgres_data_preflight(sqlite, manifest)
        numeric_preflight = build_postgres_numeric_data_preflight(sqlite, manifest)
        plan = build_postgres_ddl_plan(manifest, data_preflight, numeric_preflight)
        if plan["blocker_count"]:
            raise RuntimeError(f"PostgreSQL schema plan has {plan['blocker_count']} blocker(s); refusing to apply.")
        source_counts = _source_counts(manifest)
        report: dict[str, Any] = {
            "source_schema_sha256": manifest["schema_sha256"],
            "source_counts": source_counts,
            "boolean_preflight": data_preflight,
            "numeric_preflight": numeric_preflight,
            "ddl_statement_count": plan["statement_count"],
            "blocker_count": plan["blocker_count"],
            "applied": False,
            "verified": False,
        }

        import psycopg
        from psycopg import sql
        with psycopg.connect(dsn, autocommit=False) as pg:
            with pg.cursor() as cursor:
                if target_schema:
                    cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(target_schema)))
                schema = cursor.execute("SELECT current_schema()").fetchone()[0]
                if not schema:
                    raise RuntimeError("PostgreSQL target schema does not exist or is not visible in search_path.")
                existing_objects = cursor.execute(
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
                if int(existing_objects):
                    raise RuntimeError("PostgreSQL target schema is not empty; refusing to overwrite existing objects.")
                if apply:
                    for statement in plan["statements"]:
                        cursor.execute(statement)
                    target_counts = _target_counts(cursor)
                    report["target_counts"] = target_counts
                    mismatches = {
                        key: {"source": value, "target": target_counts.get(key)}
                        for key, value in source_counts.items()
                        if target_counts.get(key) != value
                    }
                    expected_tables = {
                        str(table["name"]): [
                            str(column["name"]) for column in table.get("columns", [])
                            if int(column.get("hidden", 0)) == 0
                        ]
                        for table in manifest.get("tables", [])
                    }
                    actual_tables = {}
                    table_names = cursor.execute(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY table_name",
                        (schema,),
                    ).fetchall()
                    for table_row in table_names:
                        table_name = table_row[0]
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
                    pg.commit()
                    report["applied"] = True
                    report["verified"] = True
                else:
                    pg.rollback()
                    report["target_schema_empty"] = True
        return report
    finally:
        sqlite.close()


def _boolean_preflight(connection: sqlite3.Connection, manifest: dict[str, Any]) -> dict[str, Any]:
    from db_schema_audit import build_postgres_data_preflight
    return build_postgres_data_preflight(connection, manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a PostgreSQL schema from a healthy SQLite source.")
    parser.add_argument("--source", required=True, help="Verified SQLite backup path")
    parser.add_argument("--apply", action="store_true", help="Apply DDL; without this flag the command is dry-run")
    parser.add_argument("--target-schema", help="Existing empty PostgreSQL schema; defaults to the connection current_schema")
    args = parser.parse_args()
    dsn = os.getenv("HASSLE_POSTGRES_MIGRATION_DSN")
    if not dsn:
        parser.error("Set HASSLE_POSTGRES_MIGRATION_DSN in the environment; its value is never printed.")
    print(json.dumps(apply_schema(args.source, dsn, args.apply, args.target_schema), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
