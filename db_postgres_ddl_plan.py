"""Reviewable PostgreSQL DDL plan generated from a SQLite schema manifest.

No SQL is executed. Unsupported or semantically uncertain features are
reported as blockers instead of being silently dropped.
"""
from __future__ import annotations

import re
from typing import Any

from db_postgres_migration_plan import build_postgres_migration_plan


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _constraint_name(table: str, kind: str, number: int) -> str:
    raw = f"mig_{table}_{kind}_{number}"
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", raw)
    return safe[:60]


def build_postgres_ddl_plan(manifest: dict[str, Any]) -> dict[str, Any]:
    """Render candidate CREATE/ALTER statements and explicit blockers."""
    structural = build_postgres_migration_plan(manifest)
    source_tables = {str(item.get("name", "")): item for item in manifest.get("tables", [])}
    planned_tables = {item["table"]: item for item in structural["tables"]}
    statements: list[str] = []
    blockers: list[dict[str, str]] = []
    deferred_fk_count = 0

    for table_name in structural["table_creation_order"]:
        source = source_tables[table_name]
        planned = planned_tables[table_name]
        ddl = str(source.get("sql") or "")
        if "WITHOUT ROWID" in ddl.upper():
            blockers.append({"table": table_name, "feature": "WITHOUT ROWID", "reason": "Requires explicit key/storage semantics review."})
        definitions: list[str] = []
        for column in source.get("columns", []):
            if int(column.get("hidden", 0)) != 0:
                blockers.append({"table": table_name, "feature": f"hidden/generated column {column.get('name')}", "reason": "Generated/hidden column semantics must be translated explicitly."})
                continue
            column_name = str(column.get("name", ""))
            candidate = next((item for item in planned["columns"] if item["name"] == column_name), None)
            if candidate is None:
                continue
            definition = f"{_q(column_name)} {candidate['postgresql_type_candidate']}"
            if candidate["not_null"]:
                definition += " NOT NULL"
            if candidate["default_requires_review"]:
                blockers.append({"table": table_name, "feature": f"default on {column_name}", "reason": f"SQLite default {candidate['sqlite_default_expression']!r} is not translated automatically."})
            definitions.append(definition)

        if planned["primary_key"]:
            definitions.append("PRIMARY KEY (" + ", ".join(_q(name) for name in planned["primary_key"]) + ")")

        for check_number, expression in enumerate(planned["check_constraints"], start=1):
            definitions.append(f"CONSTRAINT {_q(_constraint_name(table_name, 'check', check_number))} CHECK ({expression})")

        for index in source.get("indexes", []):
            if index.get("origin") == "u" and index.get("sql") is None:
                names = [str(col["name"]) for col in index.get("columns", []) if col.get("key") and col.get("name")]
                if names:
                    definitions.append("UNIQUE (" + ", ".join(_q(name) for name in names) + ")")

        statements.append(
            f"CREATE TABLE {_q(table_name)} (\n  " + ",\n  ".join(definitions) + "\n)"
        )

    for table_name in structural["table_creation_order"]:
        source = source_tables[table_name]
        planned = planned_tables[table_name]
        for fk_number, fk in enumerate(planned["foreign_keys"], start=1):
            local_columns = [item["from"] for item in fk["columns"]]
            remote_columns = [item["to"] for item in fk["columns"]]
            if not all(local_columns) or not all(remote_columns):
                blockers.append({"table": table_name, "feature": "foreign key with implicit target column", "reason": "SQLite omitted a referenced column; resolve against the referenced primary key before DDL."})
                continue
            constraint = _q(_constraint_name(table_name, "fk", fk_number))
            statement = (
                f"ALTER TABLE {_q(table_name)} ADD CONSTRAINT {constraint} "
                f"FOREIGN KEY (" + ", ".join(_q(name) for name in local_columns) + ") "
                f"REFERENCES {_q(fk['referenced_table'])} (" + ", ".join(_q(name) for name in remote_columns) + ")"
            )
            allowed_actions = {"NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"}
            on_delete = str(fk["on_delete"]).upper()
            on_update = str(fk["on_update"]).upper()
            if on_delete not in allowed_actions or on_update not in allowed_actions:
                blockers.append({"table": table_name, "feature": "foreign-key action", "reason": f"Unsupported action: {on_delete}/{on_update}"})
                continue
            statement += f" ON DELETE {on_delete} ON UPDATE {on_update} DEFERRABLE INITIALLY DEFERRED"
            statements.append(statement)
            if table_name in structural["cyclic_tables"] and fk["referenced_table"] in structural["cyclic_tables"]:
                deferred_fk_count += 1

    for table_name in structural["table_creation_order"]:
        source = source_tables[table_name]
        for index in source.get("indexes", []):
            if index.get("origin") in {"pk", "u"} and index.get("sql") is None:
                continue
            sql = index.get("sql")
            if not sql:
                continue
            statements.append(str(sql))
            if index.get("partial"):
                blockers.append({"table": table_name, "feature": f"partial index {index.get('name')}", "reason": "Index predicate/expression requires PostgreSQL semantic review."})

    for item in manifest.get("objects", []):
        if item.get("type") == "trigger":
            blockers.append({"table": str(item.get("table", "")), "feature": f"trigger {item.get('name')}", "reason": "SQLite trigger requires a separately tested PostgreSQL trigger function and trigger."})
        elif item.get("type") == "view":
            blockers.append({"table": str(item.get("table", "")), "feature": f"view {item.get('name')}", "reason": "View SQL requires PostgreSQL syntax and dependency review."})

    return {
        "format_version": 1,
        "target_engine": "postgresql",
        "source_schema_sha256": structural["source_schema_sha256"],
        "table_count": structural["table_count"],
        "statement_count": len(statements),
        "statements": statements,
        "deferred_foreign_key_count": deferred_fk_count,
        "blocker_count": len(blockers),
        "blockers": blockers,
        "execution_enabled": False,
        "ready_for_cutover": False,
        "scope_note": "Candidate DDL only. Defaults, triggers, views, partial indexes, and unsupported column features remain explicit blockers; nothing is executed.",
    }
