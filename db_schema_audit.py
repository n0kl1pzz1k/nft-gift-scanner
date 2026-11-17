"""Read-only SQLite schema inventory for safe database migration planning.

The manifest contains schema metadata only. It never reads or exports table rows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def build_schema_manifest(connection: sqlite3.Connection) -> dict[str, Any]:
    """Return a deterministic, data-free description of a SQLite schema."""
    connection.row_factory = sqlite3.Row
    objects = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE type IN ('table', 'index', 'view', 'trigger')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()

    tables: list[dict[str, Any]] = []
    for obj in objects:
        if obj["type"] != "table":
            continue
        table_name = str(obj["name"])
        quoted = _quote_identifier(table_name)
        columns = [
            {
                "cid": int(row["cid"]),
                "name": str(row["name"]),
                "type": str(row["type"] or ""),
                "not_null": bool(row["notnull"]),
                "default": row["dflt_value"],
                "primary_key_position": int(row["pk"]),
                "hidden": int(row["hidden"]),
            }
            for row in connection.execute(f"PRAGMA table_xinfo({quoted})")
        ]
        foreign_keys = [
            {
                "id": int(row["id"]),
                "sequence": int(row["seq"]),
                "referenced_table": str(row["table"]),
                "from_column": str(row["from"]),
                "to_column": str(row["to"]),
                "on_update": str(row["on_update"]),
                "on_delete": str(row["on_delete"]),
                "match": str(row["match"]),
            }
            for row in connection.execute(f"PRAGMA foreign_key_list({quoted})")
        ]
        indexes: list[dict[str, Any]] = []
        for index in connection.execute(f"PRAGMA index_list({quoted})"):
            index_name = str(index["name"])
            index_quoted = _quote_identifier(index_name)
            index_sql_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                (index_name,),
            ).fetchone()
            indexes.append(
                {
                    "name": index_name,
                    "unique": bool(index["unique"]),
                    "origin": str(index["origin"]),
                    "partial": bool(index["partial"]),
                    "columns": [
                        {
                            "sequence": int(col["seqno"]),
                            "column_id": int(col["cid"]),
                            "name": col["name"],
                            "descending": bool(col["desc"]),
                            "collation": str(col["coll"] or ""),
                            "key": bool(col["key"]),
                        }
                        for col in connection.execute(f"PRAGMA index_xinfo({index_quoted})")
                    ],
                    "sql": index_sql_row["sql"] if index_sql_row else None,
                }
            )
        indexes.sort(key=lambda item: item["name"])
        tables.append(
            {
                "name": table_name,
                "sql": obj["sql"],
                "columns": columns,
                "foreign_keys": foreign_keys,
                "indexes": indexes,
            }
        )

    other_objects = [
        {
            "type": str(obj["type"]),
            "name": str(obj["name"]),
            "table": str(obj["tbl_name"]),
            "sql": obj["sql"],
        }
        for obj in objects
        if obj["type"] != "table"
    ]
    payload: dict[str, Any] = {
        "format_version": 1,
        "engine": "sqlite",
        "sqlite_version": sqlite3.sqlite_version,
        "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
        "tables": tables,
        "objects": other_objects,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["schema_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def build_runtime_inventory(connection: sqlite3.Connection) -> dict[str, Any]:
    """Describe the live schema and per-table row counts without reading row values."""
    manifest = build_schema_manifest(connection)
    tables = []
    for table in manifest["tables"]:
        quoted = _quote_identifier(str(table["name"]))
        row_count = int(connection.execute(
            f"SELECT COUNT(*) FROM {quoted}"
        ).fetchone()[0])
        tables.append({
            "name": table["name"],
            "row_count": row_count,
            "columns": [
                {"name": column["name"], "type": column["type"],
                 "not_null": column["not_null"],
                 "primary_key_position": column["primary_key_position"]}
                for column in table["columns"]
            ],
            "indexes": [
                {"name": index["name"], "unique": index["unique"],
                 "partial": index["partial"]}
                for index in table["indexes"]
            ],
            "foreign_keys": table["foreign_keys"],
        })
    object_counts: dict[str, int] = {}
    object_names: dict[str, list[dict[str, str]]] = {}
    for item in manifest["objects"]:
        kind = str(item["type"])
        object_counts[kind] = object_counts.get(kind, 0) + 1
        object_names.setdefault(kind, []).append({
            "name": str(item["name"]),
            "table": str(item["table"]),
        })
    return {
        "engine": manifest["engine"],
        "sqlite_version": manifest["sqlite_version"],
        "user_version": manifest["user_version"],
        "schema_sha256": manifest["schema_sha256"],
        "table_count": len(tables),
        "total_rows": sum(table["row_count"] for table in tables),
        "tables": tables,
        "object_counts": object_counts,
        "objects": object_names,
        "scope_note": "Schema metadata and row counts only; no application row values are read or returned.",
    }


def audit_database(database_path: str | Path) -> dict[str, Any]:
    """Open an existing database read-only and inventory its schema."""
    path = Path(database_path).expanduser().resolve(strict=True)
    uri = f"file:{path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        return build_schema_manifest(connection)
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a data-free SQLite schema manifest.")
    parser.add_argument("--database", required=True, help="Path to an existing SQLite database")
    parser.add_argument("--output", help="Optional JSON output path; stdout is used by default")
    args = parser.parse_args()
    manifest = audit_database(args.database)
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).expanduser().write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0




def build_postgres_portability_report(manifest: dict[str, Any]) -> dict[str, Any]:
    """Classify schema features that need explicit PostgreSQL migration work.

    This is a schema-only assessment: it does not inspect row data or claim
    that application SQL/transaction semantics are PostgreSQL-compatible.
    """
    tables = manifest.get("tables", [])
    objects = manifest.get("objects", [])
    trigger_items = [
        {"name": str(item.get("name", "")), "table": str(item.get("table", ""))}
        for item in objects
        if item.get("type") == "trigger"
    ]
    view_items = [
        {"name": str(item.get("name", "")), "table": str(item.get("table", ""))}
        for item in objects
        if item.get("type") == "view"
    ]
    autoincrement_columns: list[dict[str, str]] = []
    partial_indexes: list[dict[str, str]] = []
    check_constraints: list[dict[str, str]] = []

    for table in tables:
        table_name = str(table.get("name", ""))
        ddl = str(table.get("sql") or "")
        if "AUTOINCREMENT" in ddl.upper():
            for column in table.get("columns", []):
                if int(column.get("primary_key_position", 0)) > 0 and str(column.get("type", "")).upper() == "INTEGER":
                    autoincrement_columns.append({
                        "table": table_name,
                        "column": str(column.get("name", "")),
                    })
        if "CHECK" in ddl.upper():
            check_constraints.append({"table": table_name})
        for index in table.get("indexes", []):
            if index.get("partial"):
                partial_indexes.append({
                    "table": table_name,
                    "name": str(index.get("name", "")),
                    "sql": str(index.get("sql") or ""),
                })

    blockers = []
    if trigger_items:
        blockers.append({
            "feature": "sqlite_triggers",
            "count": len(trigger_items),
            "reason": "SQLite trigger bodies and functions must be reviewed and rewritten for PostgreSQL.",
            "items": trigger_items,
        })
    if autoincrement_columns:
        blockers.append({
            "feature": "sqlite_autoincrement",
            "count": len(autoincrement_columns),
            "reason": "Map SQLite AUTOINCREMENT columns to PostgreSQL identity/sequence semantics and preserve IDs.",
            "items": autoincrement_columns,
        })

    return {
        "format_version": 1,
        "source_engine": str(manifest.get("engine", "unknown")),
        "target_engine": "postgresql",
        "schema_sha256": manifest.get("schema_sha256"),
        "table_count": len(tables),
        "foreign_key_count": sum(len(table.get("foreign_keys", [])) for table in tables),
        "trigger_count": len(trigger_items),
        "view_count": len(view_items),
        "partial_indexes": partial_indexes,
        "check_constraint_tables": check_constraints,
        "blockers": blockers,
        "review_items": [
            "Recreate and verify all foreign keys, unique constraints and CHECK constraints.",
            "Translate partial-index predicates and confirm their intended NULL/status behavior.",
            "Recreate views and validate their SQL against PostgreSQL.",
            "Audit application SQL, placeholders, row access, conflict handling and transaction boundaries separately.",
            "Verify sequence values after importing preserved primary keys.",
        ],
        "ready_for_cutover": False,
        "scope_note": "Schema-only report. A clean report does not establish application-query, transaction, data, or operational compatibility.",
    }


if __name__ == "__main__":
    raise SystemExit(main())
