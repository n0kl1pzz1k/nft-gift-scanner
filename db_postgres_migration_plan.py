"""Deterministic PostgreSQL migration ordering derived from a SQLite schema manifest.

This module produces a reviewable migration plan only. It never connects to
PostgreSQL, reads application rows, executes DDL, or authorizes cutover.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from db_schema_audit import _extract_check_constraints, _postgres_type_candidate


def _foreign_key_groups(table: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in table.get("foreign_keys", []):
        grouped.setdefault(int(item.get("id", 0)), []).append(item)
    result = []
    for key, members in sorted(grouped.items()):
        members.sort(key=lambda item: int(item.get("sequence", 0)))
        first = members[0]
        result.append({
            "id": key,
            "referenced_table": str(first.get("referenced_table", "")),
            "columns": [
                {"from": str(member.get("from_column", "")),
                 "to": str(member.get("to_column", ""))}
                for member in members
            ],
            "on_delete": str(first.get("on_delete", "NO ACTION")),
            "on_update": str(first.get("on_update", "NO ACTION")),
        })
    return result


def _topological_order(table_names: list[str], foreign_keys: dict[str, list[dict[str, Any]]]) -> tuple[list[str], set[str]]:
    """Order referenced tables before dependants; return cyclic table names."""
    names = set(table_names)
    dependencies = {
        name: {
            str(fk["referenced_table"])
            for fk in foreign_keys.get(name, [])
            if str(fk["referenced_table"]) in names and str(fk["referenced_table"]) != name
        }
        for name in table_names
    }
    ordered: list[str] = []
    remaining = set(names)
    while remaining:
        ready = sorted(name for name in remaining if not (dependencies[name] & remaining))
        if not ready:
            break
        ordered.extend(ready)
        remaining.difference_update(ready)
    return ordered, remaining


def _implicit_rowid_primary_key_indexes(table: dict[str, Any]) -> int:
    """Count PostgreSQL PK indexes absent from SQLite's rowid-alias index list."""
    ddl = str(table.get("sql") or "").upper()
    if "WITHOUT ROWID" in ddl:
        return 0
    primary = [
        column for column in table.get("columns", [])
        if int(column.get("primary_key_position", 0)) > 0
    ]
    if len(primary) != 1 or str(primary[0].get("type", "")).strip().upper() != "INTEGER":
        return 0
    if any(index.get("origin") == "pk" for index in table.get("indexes", [])):
        return 0
    return 1


def build_postgres_migration_plan(manifest: dict[str, Any]) -> dict[str, Any]:
    """Build a data-free table/constraint migration order from a SQLite manifest."""
    tables = list(manifest.get("tables", []))
    names = [str(table.get("name", "")) for table in tables]
    foreign_keys = {
        str(table.get("name", "")): _foreign_key_groups(table)
        for table in tables
    }
    order, cyclic_tables = _topological_order(names, foreign_keys)

    table_plans: list[dict[str, Any]] = []
    deferred_foreign_keys: list[dict[str, Any]] = []
    for table in sorted(tables, key=lambda item: str(item.get("name", ""))):
        name = str(table.get("name", ""))
        ddl = str(table.get("sql") or "")
        columns = []
        for column in table.get("columns", []):
            mapping = _postgres_type_candidate(name, column, ddl)
            columns.append({
                "name": str(column.get("name", "")),
                "postgresql_type_candidate": mapping["postgresql_type_candidate"],
                "mapping_confidence": mapping["mapping_confidence"],
                "not_null": bool(column.get("not_null")),
                "primary_key_position": int(column.get("primary_key_position", 0)),
                "sqlite_default_expression": column.get("default"),
                "default_requires_review": column.get("default") is not None,
            })

        table_fks = foreign_keys.get(name, [])
        for fk in table_fks:
            if name in cyclic_tables and str(fk["referenced_table"]) in cyclic_tables:
                deferred_foreign_keys.append({
                    "table": name,
                    **fk,
                    "reason": "Foreign-key edge is inside a dependency cycle; add after table creation and data load.",
                })

        indexes = []
        for index in table.get("indexes", []):
            indexes.append({
                "name": str(index.get("name", "")),
                "unique": bool(index.get("unique")),
                "partial": bool(index.get("partial")),
                "source_sql": index.get("sql"),
                "requires_manual_review": bool(index.get("partial")) or not bool(index.get("sql")),
            })

        table_plans.append({
            "table": name,
            "columns": columns,
            "primary_key": [
                column["name"] for column in sorted(
                    (item for item in columns if item["primary_key_position"] > 0),
                    key=lambda item: item["primary_key_position"],
                )
            ],
            "foreign_keys": table_fks,
            "check_constraints": _extract_check_constraints(ddl),
            "indexes": indexes,
            "has_autoincrement": "AUTOINCREMENT" in ddl.upper(),
        })

    triggers = [
        {"name": str(item.get("name", "")), "table": str(item.get("table", "")),
         "source_sql": item.get("sql"), "requires_postgresql_rewrite": True}
        for item in manifest.get("objects", [])
        if item.get("type") == "trigger"
    ]
    views = [
        {"name": str(item.get("name", "")), "table": str(item.get("table", "")),
         "source_sql": item.get("sql"), "requires_postgresql_review": True}
        for item in manifest.get("objects", [])
        if item.get("type") == "view"
    ]

    return {
        "format_version": 1,
        "source_engine": str(manifest.get("engine", "unknown")),
        "target_engine": "postgresql",
        "source_schema_sha256": manifest.get("schema_sha256"),
        "table_count": len(tables),
        "table_creation_order": order + sorted(cyclic_tables),
        "cyclic_table_count": len(cyclic_tables),
        "cyclic_tables": sorted(cyclic_tables),
        "tables": table_plans,
        "deferred_foreign_keys": deferred_foreign_keys,
        "trigger_rewrite_count": len(triggers),
        "triggers_requiring_rewrite": triggers,
        "views_requiring_review": views,
        "sqlite_index_count": sum(len(item["indexes"]) for item in table_plans),
        "implicit_rowid_primary_key_index_count": sum(_implicit_rowid_primary_key_indexes(table) for table in tables),
        "index_count": sum(len(item["indexes"]) for item in table_plans) + sum(_implicit_rowid_primary_key_indexes(table) for table in tables),
        "ready_for_cutover": False,
        "execution_enabled": False,
        "scope_note": (
            "Data-free structural plan only. It does not emit executable DDL, "
            "connect to PostgreSQL, migrate rows, or authorize production cutover."
        ),
    }



def main() -> int:
    parser = argparse.ArgumentParser(description="Create a data-free PostgreSQL migration plan from SQLite.")
    parser.add_argument("--database", required=True, help="Path to an existing SQLite database or verified backup")
    parser.add_argument("--output", help="Optional JSON output path; stdout is used by default")
    args = parser.parse_args()

    from db_schema_audit import audit_database

    plan = build_postgres_migration_plan(audit_database(args.database))
    rendered = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).expanduser().write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
