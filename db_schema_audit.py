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
            "create_sql": table["sql"],
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
            "create_sql": item["sql"],
        })
    from db_postgres_migration_plan import build_postgres_migration_plan
    from db_postgres_ddl_plan import build_postgres_ddl_plan
    from db_sql_compat_audit import audit_source_tree

    boolean_preflight = build_postgres_data_preflight(connection, manifest)
    ddl_plan = build_postgres_ddl_plan(manifest, boolean_preflight)
    runtime_compatibility = audit_source_tree(Path(__file__).resolve().parent)

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
        "postgresql_portability": build_postgres_portability_report(manifest),
        "postgresql_migration_plan": build_postgres_migration_plan(manifest),
        "postgresql_ddl_plan": ddl_plan,
        "postgresql_data_preflight": boolean_preflight,
        "postgresql_runtime_compatibility": runtime_compatibility,
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




def _sqlite_type_affinity(declared_type: str) -> str:
    """Apply SQLite's documented declared-type affinity rules."""
    normalized = declared_type.upper()
    if "INT" in normalized:
        return "INTEGER"
    if any(token in normalized for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if not normalized or "BLOB" in normalized:
        return "BLOB"
    if any(token in normalized for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _postgres_type_candidate(table_name: str, column: dict[str, Any], table_ddl: str) -> dict[str, str]:
    """Suggest a conservative PostgreSQL type without inspecting stored values."""
    name = str(column.get("name", ""))
    declared_type = str(column.get("type", ""))
    affinity = _sqlite_type_affinity(declared_type)
    is_integer_pk = (
        affinity == "INTEGER"
        and int(column.get("primary_key_position", 0)) > 0
    )
    if is_integer_pk and "AUTOINCREMENT" in table_ddl.upper():
        return {
            "postgresql_type_candidate": "BIGINT GENERATED BY DEFAULT AS IDENTITY",
            "mapping_confidence": "conditional",
            "mapping_reason": "Preserves SQLite 64-bit integer key range and requires explicit sequence high-water restoration.",
        }
    if affinity == "INTEGER":
        boolean_columns = {
            "is_enabled", "can_view_gifts_and_stars",
            "can_transfer_and_upgrade_gifts", "can_transfer_stars",
            "is_premium",
        }
        if name in boolean_columns:
            return {
                "postgresql_type_candidate": "BOOLEAN",
                "mapping_confidence": "conditional",
                "mapping_reason": "Boolean-like permission/flag field; verify every stored value is exactly 0 or 1 before conversion.",
            }
        return {
            "postgresql_type_candidate": "BIGINT",
            "mapping_confidence": "conservative",
            "mapping_reason": "Preserves SQLite signed 64-bit integer values; timestamp epochs and counters remain numeric until application semantics are migrated.",
        }
    if affinity == "TEXT":
        return {
            "postgresql_type_candidate": "TEXT",
            "mapping_confidence": "conservative",
            "mapping_reason": "Preserves text identifiers and serialized payloads without lossy coercion; JSONB conversion requires separate validation.",
        }
    if affinity == "REAL":
        return {
            "postgresql_type_candidate": "DOUBLE PRECISION",
            "mapping_confidence": "conditional",
            "mapping_reason": "Approximate numeric mapping; verify precision requirements and stored values.",
        }
    if affinity == "NUMERIC":
        return {
            "postgresql_type_candidate": "NUMERIC",
            "mapping_confidence": "conditional",
            "mapping_reason": "Exact numeric candidate; precision and scale must be explicitly selected after data profiling.",
        }
    return {
        "postgresql_type_candidate": "BYTEA",
        "mapping_confidence": "conditional",
        "mapping_reason": "Binary candidate; verify storage format and payload size before conversion.",
    }



def _extract_check_constraints(table_ddl: str) -> list[str]:
    """Extract CHECK expressions from CREATE TABLE SQL without parsing literals.

    Handles nested parentheses, quoted strings/identifiers, and SQL comments.
    The returned expressions are source DDL fragments for migration review.
    """
    expressions: list[str] = []
    index = 0
    length = len(table_ddl)
    while index < length:
        char = table_ddl[index]
        if char in ("'", '"', "`", "["):
            closing = "]" if char == "[" else char
            index += 1
            while index < length:
                if table_ddl[index] == closing:
                    if closing in ("'", '"', "`") and index + 1 < length and table_ddl[index + 1] == closing:
                        index += 2
                        continue
                    index += 1
                    break
                if closing == "'" and table_ddl[index] == "\\" and index + 1 < length:
                    index += 2
                else:
                    index += 1
            continue
        if table_ddl.startswith("--", index):
            newline = table_ddl.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if table_ddl.startswith("/*", index):
            end_comment = table_ddl.find("*/", index + 2)
            index = length if end_comment < 0 else end_comment + 2
            continue
        if table_ddl[index:index + 5].upper() == "CHECK":
            before_ok = index == 0 or not (table_ddl[index - 1].isalnum() or table_ddl[index - 1] == "_")
            after = index + 5
            after_ok = after >= length or not (table_ddl[after].isalnum() or table_ddl[after] == "_")
            if before_ok and after_ok:
                cursor = after
                while cursor < length and table_ddl[cursor].isspace():
                    cursor += 1
                if cursor < length and table_ddl[cursor] == "(":
                    start = cursor + 1
                    depth = 1
                    cursor += 1
                    quote: str | None = None
                    while cursor < length and depth:
                        current = table_ddl[cursor]
                        if quote:
                            closing = "]" if quote == "[" else quote
                            if current == closing:
                                if closing in ("'", '"', "`") and cursor + 1 < length and table_ddl[cursor + 1] == closing:
                                    cursor += 2
                                    continue
                                quote = None
                            elif quote == "'" and current == "\\" and cursor + 1 < length:
                                cursor += 2
                                continue
                        elif current in ("'", '"', "`", "["):
                            quote = current
                        elif table_ddl.startswith("--", cursor):
                            newline = table_ddl.find("\n", cursor + 2)
                            cursor = length if newline < 0 else newline + 1
                            continue
                        elif table_ddl.startswith("/*", cursor):
                            end_comment = table_ddl.find("*/", cursor + 2)
                            cursor = length if end_comment < 0 else end_comment + 2
                            continue
                        elif current == "(":
                            depth += 1
                        elif current == ")":
                            depth -= 1
                        cursor += 1
                    if depth == 0:
                        expressions.append(table_ddl[start:cursor - 1].strip())
                        index = cursor
                        continue
        index += 1
    return expressions



def _trigger_postgres_migration_plan(name: str, table: str, ddl: str) -> dict[str, Any]:
    """Classify a SQLite trigger and describe its PostgreSQL rewrite requirements."""
    normalized_name = name.lower()
    normalized_sql = ddl.lower()
    if "no_delete" in normalized_name or "no_update" in normalized_name:
        category = "immutable_guard"
        strategy = "PostgreSQL BEFORE trigger function that raises an exception; preserve both UPDATE and DELETE protections."
        confidence = "high"
    elif "offer_received" in normalized_name or "offer_status_notification" in normalized_name:
        category = "notification_side_effect"
        strategy = "PostgreSQL AFTER trigger function inserting the notification in the same transaction; replace randomblob IDs with gen_random_uuid() or an approved cryptographic ID generator."
        confidence = "high"
    elif "cancel_declines_offers" in normalized_name:
        category = "dependent_state_update"
        strategy = "PostgreSQL AFTER UPDATE trigger with a guarded UPDATE of pending offers; verify transition predicates and affected-row semantics."
        confidence = "high"
    elif "verified_assets_metadata_immutable" in normalized_name:
        category = "conditional_immutability_guard"
        strategy = "PostgreSQL BEFORE UPDATE trigger function comparing protected metadata and allowing only the explicitly approved ownership-transfer transition."
        confidence = "high"
    else:
        category = "custom_trigger"
        strategy = "Manual rewrite required; review source DDL and prove equivalent timing, predicates, side effects and rollback behavior."
        confidence = "low"

    uses_randomblob = "randomblob(" in normalized_sql
    if uses_randomblob:
        strategy += " SQLite randomblob() is unsupported; use PostgreSQL gen_random_uuid() or pgcrypto gen_random_bytes() with a tested text encoding."
    return {
        "name": name,
        "table": table,
        "category": category,
        "postgresql_strategy": strategy,
        "uses_sqlite_randomblob": uses_randomblob,
        "review_confidence": confidence,
        "source_ddl": ddl,
    }


def build_postgres_portability_report(manifest: dict[str, Any]) -> dict[str, Any]:
    """Classify schema features that need explicit PostgreSQL migration work.

    This is a schema-only assessment: it does not inspect row data or claim
    that application SQL/transaction semantics are PostgreSQL-compatible.
    """
    tables = manifest.get("tables", [])
    objects = manifest.get("objects", [])
    trigger_items = [
        _trigger_postgres_migration_plan(
            str(item.get("name", "")),
            str(item.get("table", "")),
            str(item.get("sql") or ""),
        )
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
    check_constraint_details: list[dict[str, Any]] = []
    column_type_affinities: list[dict[str, str]] = []
    foreign_key_details: list[dict[str, Any]] = []
    unique_indexes: list[dict[str, Any]] = []
    ordinary_indexes: list[dict[str, Any]] = []
    primary_keys: list[dict[str, Any]] = []

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
        primary_key_columns = sorted(
            [
                {"column": str(column.get("name", "")), "position": int(column.get("primary_key_position", 0))}
                for column in table.get("columns", [])
                if int(column.get("primary_key_position", 0)) > 0
            ],
            key=lambda item: item["position"],
        )
        if primary_key_columns:
            primary_keys.append({"table": table_name, "columns": primary_key_columns})

        grouped_foreign_keys: dict[int, list[dict[str, Any]]] = {}
        for foreign_key in table.get("foreign_keys", []):
            grouped_foreign_keys.setdefault(int(foreign_key.get("id", 0)), []).append(foreign_key)
        for foreign_key_id, members in sorted(grouped_foreign_keys.items()):
            members.sort(key=lambda item: int(item.get("sequence", 0)))
            foreign_key_details.append({
                "table": table_name,
                "id": foreign_key_id,
                "referenced_table": str(members[0].get("referenced_table", "")),
                "columns": [
                    {
                        "from": str(member.get("from_column", "")),
                        "to": str(member.get("to_column", "")),
                        "sequence": int(member.get("sequence", 0)),
                    }
                    for member in members
                ],
                "on_update": str(members[0].get("on_update", "")),
                "on_delete": str(members[0].get("on_delete", "")),
                "match": str(members[0].get("match", "")),
            })

        extracted_checks = _extract_check_constraints(ddl)
        if extracted_checks:
            check_constraints.append({"table": table_name})
            for ordinal, expression in enumerate(extracted_checks, start=1):
                check_constraint_details.append({
                    "table": table_name,
                    "ordinal": ordinal,
                    "expression": expression,
                })
        for column in table.get("columns", []):
            declared_type = str(column.get("type", ""))
            mapping = _postgres_type_candidate(table_name, column, ddl)
            column_type_affinities.append({
                "table": table_name,
                "column": str(column.get("name", "")),
                "declared_type": declared_type,
                "sqlite_affinity": _sqlite_type_affinity(declared_type),
                **mapping,
            })
        for index in table.get("indexes", []):
            index_record = {
                "table": table_name,
                "name": str(index.get("name", "")),
                "unique": bool(index.get("unique")),
                "origin": str(index.get("origin", "")),
                "partial": bool(index.get("partial")),
                "columns": [
                    {
                        "name": column.get("name"),
                        "sequence": int(column.get("sequence", 0)),
                        "descending": bool(column.get("descending")),
                        "collation": str(column.get("collation", "")),
                        "key": bool(column.get("key")),
                    }
                    for column in index.get("columns", [])
                ],
                "sql": index.get("sql"),
            }
            if index_record["unique"]:
                unique_indexes.append(index_record)
            else:
                ordinary_indexes.append(index_record)
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
            "reason": "SQLite trigger bodies and functions must be rewritten and behaviorally verified for PostgreSQL.",
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
        "foreign_key_count": len(foreign_key_details),
        "foreign_keys": foreign_key_details,
        "primary_keys": primary_keys,
        "unique_indexes": unique_indexes,
        "ordinary_indexes": ordinary_indexes,
        "trigger_count": len(trigger_items),
        "trigger_migration_plan": trigger_items,
        "sqlite_randomblob_trigger_count": sum(item["uses_sqlite_randomblob"] for item in trigger_items),
        "view_count": len(view_items),
        "partial_indexes": partial_indexes,
        "check_constraint_tables": check_constraints,
        "check_constraint_count": len(check_constraint_details),
        "check_constraints": check_constraint_details,
        "column_type_affinities": column_type_affinities,
        "type_mapping_review_count": len(column_type_affinities),
        "blockers": blockers,
        "review_items": [
            "Recreate and verify all foreign keys, unique constraints and CHECK constraints.",
            "Translate partial-index predicates and confirm their intended NULL/status behavior.",
            "Recreate views and validate their SQL against PostgreSQL.",
            "Review every suggested PostgreSQL type against stored values and application semantics; SQLite affinity is not a lossless type mapping, and suggestions do not authorize cutover.",
            "Audit application SQL, placeholders, row access, conflict handling and transaction boundaries separately.",
            "Verify sequence values after importing preserved primary keys.",
        ],
        "ready_for_cutover": False,
        "scope_note": "Schema-only report. A clean report does not establish application-query, transaction, data, or operational compatibility.",
    }



def build_postgres_data_preflight(
    connection: sqlite3.Connection,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Profile aggregate safety conditions for conditional PostgreSQL BOOLEAN mappings.

    No application values are returned. Only counts are reported.
    """
    schema = manifest or build_schema_manifest(connection)
    profiles: list[dict[str, Any]] = []
    for table in schema.get("tables", []):
        table_name = str(table.get("name", ""))
        table_ddl = str(table.get("sql") or "")
        for column in table.get("columns", []):
            mapping = _postgres_type_candidate(table_name, column, table_ddl)
            if mapping["postgresql_type_candidate"] != "BOOLEAN":
                continue
            column_name = str(column.get("name", ""))
            quoted_table = _quote_identifier(table_name)
            quoted_column = _quote_identifier(column_name)
            aggregate = connection.execute(
                f"SELECT COUNT(*), "
                f"COALESCE(SUM(CASE WHEN {quoted_column} IS NOT NULL "
                f"AND {quoted_column} NOT IN (0, 1) THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN {quoted_column} IS NULL THEN 1 ELSE 0 END), 0) "
                f"FROM {quoted_table}"
            ).fetchone()
            total_rows, invalid_values, null_values = (int(value) for value in aggregate)
            nullable = not bool(column.get("not_null")) and int(column.get("primary_key_position", 0)) == 0
            profiles.append({
                "table": table_name,
                "column": column_name,
                "total_rows": total_rows,
                "invalid_value_count": invalid_values,
                "null_value_count": null_values,
                "nullable": nullable,
                "boolean_conversion_safe": invalid_values == 0 and (nullable or null_values == 0),
            })

    foreign_key_participants: set[tuple[str, str]] = set()
    for table in schema.get("tables", []):
        table_name = str(table.get("name", ""))
        for foreign_key in table.get("foreign_keys", []):
            local_column = str(foreign_key.get("from_column", ""))
            referenced_table = str(foreign_key.get("referenced_table", ""))
            referenced_column = str(foreign_key.get("to_column", ""))
            if local_column:
                foreign_key_participants.add((table_name, local_column))
            if referenced_table and referenced_column:
                foreign_key_participants.add((referenced_table, referenced_column))

    integer_profiles: list[dict[str, Any]] = []
    for table in schema.get("tables", []):
        table_name = str(table.get("name", ""))
        table_ddl = str(table.get("sql") or "")
        candidates = [
            column for column in table.get("columns", [])
            if _postgres_type_candidate(table_name, column, table_ddl)["postgresql_type_candidate"]
            in {"BIGINT", "BIGINT GENERATED BY DEFAULT AS IDENTITY"}
        ]
        if not candidates:
            continue
        expressions: list[str] = []
        for column in candidates:
            quoted_column = _quote_identifier(str(column.get("name", "")))
            for storage_class in ("real", "text", "blob"):
                expressions.append(
                    "COALESCE(SUM(CASE WHEN "
                    f"typeof({quoted_column}) = '{storage_class}' THEN 1 ELSE 0 END), 0)"
                )
        quoted_table = _quote_identifier(table_name)
        aggregate = connection.execute(
            "SELECT COUNT(*), " + ", ".join(expressions) + f" FROM {quoted_table}"
        ).fetchone()
        total_rows = int(aggregate[0])
        offset = 1
        for column in candidates:
            real_count, text_count, blob_count = (
                int(aggregate[offset]), int(aggregate[offset + 1]), int(aggregate[offset + 2])
            )
            offset += 3
            column_name = str(column.get("name", ""))
            primary_key = int(column.get("primary_key_position", 0)) > 0
            foreign_key_participant = (table_name, column_name) in foreign_key_participants
            base_type = _postgres_type_candidate(table_name, column, table_ddl)["postgresql_type_candidate"]
            type_override = (
                real_count > 0
                and text_count == 0
                and blob_count == 0
                and not primary_key
                and not foreign_key_participant
            )
            candidate_type = "DOUBLE PRECISION" if type_override else base_type
            non_integer_count = real_count + text_count + blob_count
            integer_safe = (
                text_count == 0
                and blob_count == 0
                and (real_count == 0 or type_override)
            )
            integer_profiles.append({
                "table": table_name,
                "column": column_name,
                "total_rows": total_rows,
                "real_storage_count": real_count,
                "text_storage_count": text_count,
                "blob_storage_count": blob_count,
                "non_integer_storage_count": non_integer_count,
                "primary_key": primary_key,
                "foreign_key_participant": foreign_key_participant,
                "type_override": type_override,
                "postgresql_type_candidate": candidate_type,
                "integer_conversion_safe": integer_safe,
            })

    return {
        "format_version": 1,
        "target_engine": "postgresql",
        "profiled_column_count": len(profiles),
        "safe_column_count": sum(item["boolean_conversion_safe"] for item in profiles),
        "unsafe_column_count": sum(not item["boolean_conversion_safe"] for item in profiles),
        "columns": profiles,
        "integer_profiled_column_count": len(integer_profiles),
        "unsafe_integer_column_count": sum(not item["integer_conversion_safe"] for item in integer_profiles),
        "non_integer_storage_value_count": sum(item["non_integer_storage_count"] for item in integer_profiles),
        "real_backed_integer_column_count": sum(item["real_storage_count"] > 0 for item in integer_profiles),
        "integer_type_override_count": sum(item["type_override"] for item in integer_profiles),
        "integer_columns": integer_profiles,
        "ready_for_cutover": False,
        "scope_note": "Aggregate-only BOOLEAN and BIGINT storage-class profile; no application row values are returned. This does not authorize cutover.",
    }


if __name__ == "__main__":
    raise SystemExit(main())



def build_postgres_migration_gate_summary(inventory: dict[str, Any]) -> dict[str, Any]:
    """Return compact, aggregate-only migration readiness diagnostics."""
    preflight = inventory.get("postgresql_data_preflight", {})
    boolean_columns = preflight.get("columns", [])
    runtime = inventory.get("postgresql_runtime_compatibility", {})
    return {
        "schema_sha256": inventory.get("schema_sha256"),
        "table_count": int(inventory.get("table_count", 0)),
        "total_rows": int(inventory.get("total_rows", 0)),
        "index_count": int(inventory.get("object_counts", {}).get("index", 0)),
        "trigger_count": int(inventory.get("object_counts", {}).get("trigger", 0)),
        "ddl_blocker_count": int(inventory.get("postgresql_ddl_plan", {}).get("blocker_count", 0)),
        "ddl_statement_count": int(inventory.get("postgresql_ddl_plan", {}).get("statement_count", 0)),
        "translated_trigger_count": int(inventory.get("postgresql_ddl_plan", {}).get("trigger_translation_count", 0)),
        "boolean_column_count": len(boolean_columns),
        "unsafe_boolean_column_count": sum(not item.get("boolean_conversion_safe", False) for item in boolean_columns),
        "runtime_finding_count": int(runtime.get("finding_count", 0)),
        "runtime_blocker_count": int(runtime.get("blocker_count", 0)),
        "runtime_review_count": int(runtime.get("review_count", 0)),
        "runtime_feature_counts": dict(runtime.get("feature_counts", {})),
        "runtime_ready": bool(runtime.get("ready_for_postgresql_runtime", False)),
        "boolean_invalid_value_count": sum(int(item.get("invalid_value_count", 0)) for item in boolean_columns),
        "boolean_null_value_count": sum(int(item.get("null_value_count", 0)) for item in boolean_columns),
        "integer_column_count": int(preflight.get("integer_profiled_column_count", 0)),
        "unsafe_integer_column_count": int(preflight.get("unsafe_integer_column_count", 0)),
        "non_integer_storage_value_count": int(preflight.get("non_integer_storage_value_count", 0)),
        "real_backed_integer_column_count": int(preflight.get("real_backed_integer_column_count", 0)),
        "integer_type_override_count": int(preflight.get("integer_type_override_count", 0)),
        "cutover_allowed": bool(inventory.get("postgresql_ddl_plan", {}).get("ready_for_cutover", False)),
    }
