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


if __name__ == "__main__":
    raise SystemExit(main())
