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


def _translate_default(expression: Any, postgres_type: str) -> str | None:
    if expression is None:
        return None
    value = str(expression).strip()
    if postgres_type == "BOOLEAN" and value in {"0", "1"}:
        return "TRUE" if value == "1" else "FALSE"
    if re.fullmatch(r"'(?:''|[^'])*'", value):
        return value
    if re.fullmatch(r'"(?:""|[^"])*"', value):
        return "'" + value[1:-1].replace("''", "'").replace('"', "''") + "'"
    if re.fullmatch(r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?", value):
        return value
    if value.upper() in {"NULL", "TRUE", "FALSE", "CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME"}:
        return value.upper()
    return None


def _portable_partial_index(sql: str) -> bool:
    match = re.fullmatch(
        r"\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)\s+ON\s+'
        r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s+WHERE\s+(.+?)\s*;?\s*',
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return False
    predicate = match.group(1)
    return bool(re.fullmatch(r"[A-Za-z0-9_\s\"'(),.=<>!+-]+", predicate))


def _trigger_ddl(item: dict[str, Any]) -> list[str] | None:
    name = str(item.get("name", ""))
    table = str(item.get("table", ""))
    qtable = _q(table)
    function = _q(_constraint_name(name, "fn", 1))
    qtrigger = _q(name)
    prefix = f"CREATE OR REPLACE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $migration$\\n"
    suffix = "\\n$migration$"
    if name in {"trg_marketplace_deal_events_no_delete", "trg_marketplace_deal_events_no_update"}:
        action = "DELETE" if name.endswith("no_delete") else "UPDATE"
        message = "deal events are immutable"
        body = f"BEGIN\\n  RAISE EXCEPTION '{message}';\\nEND;"
        trigger = f"CREATE TRIGGER {qtrigger} BEFORE {action} ON {qtable} FOR EACH ROW EXECUTE FUNCTION {function}()"
        return [prefix + body + suffix, trigger]
    if name in {"trg_marketplace_deal_lifecycle_events_no_delete", "trg_marketplace_deal_lifecycle_events_no_update"}:
        action = "DELETE" if name.endswith("no_delete") else "UPDATE"
        body = "BEGIN\\n  RAISE EXCEPTION 'deal lifecycle events are immutable';\\nEND;"
        trigger = f"CREATE TRIGGER {qtrigger} BEFORE {action} ON {qtable} FOR EACH ROW EXECUTE FUNCTION {function}()"
        return [prefix + body + suffix, trigger]
    if name == "trg_marketplace_offer_received" and table == "marketplace_offers":
        body = """BEGIN
  INSERT INTO marketplace_notifications
    (id, user_id, event_type, offer_id, order_id, order_title, actor_name, created_at)
  SELECT 'nt-' || lower(substr(replace(gen_random_uuid()::text, '-', ''), 1, 16)),
         o.user_id, 'offer_received', NEW.id, NEW.order_id, o.title, NEW.sender_name, NEW.created_at
  FROM marketplace_orders o
  WHERE o.id = NEW.order_id AND o.user_id <> NEW.sender_user_id;
  RETURN NEW;
END;"""
        trigger = f"CREATE TRIGGER {qtrigger} AFTER INSERT ON {qtable} FOR EACH ROW EXECUTE FUNCTION {function}()"
        return [prefix + body + suffix, trigger]
    if name == "trg_marketplace_offer_status_notification" and table == "marketplace_offers":
        body = """BEGIN
  IF OLD.status = 'pending' AND NEW.status <> OLD.status
     AND NEW.status IN ('accepted', 'declined', 'withdrawn') THEN
    INSERT INTO marketplace_notifications
      (id, user_id, event_type, offer_id, order_id, order_title, actor_name, created_at)
    SELECT 'nt-' || lower(substr(replace(gen_random_uuid()::text, '-', ''), 1, 16)),
           CASE WHEN NEW.status = 'withdrawn' THEN o.user_id ELSE NEW.sender_user_id END,
           CASE NEW.status
             WHEN 'accepted' THEN 'offer_accepted'
             WHEN 'declined' THEN 'offer_declined'
             WHEN 'withdrawn' THEN 'offer_withdrawn'
           END,
           NEW.id, NEW.order_id, o.title,
           CASE WHEN NEW.status = 'withdrawn' THEN NEW.sender_name ELSE o.owner_name END,
           NEW.updated_at
    FROM marketplace_orders o
    WHERE o.id = NEW.order_id
      AND (NEW.status <> 'withdrawn' OR o.user_id <> NEW.sender_user_id);
  END IF;
  RETURN NEW;
END;"""
        trigger = f"CREATE TRIGGER {qtrigger} AFTER UPDATE OF status ON {qtable} FOR EACH ROW WHEN (OLD.status = 'pending' AND NEW.status <> OLD.status) EXECUTE FUNCTION {function}()"
        return [prefix + body + suffix, trigger]
    if name == "trg_marketplace_order_cancel_declines_offers" and table == "marketplace_orders":
        body = """BEGIN
  IF OLD.status = 'active' AND NEW.status = 'cancelled' THEN
    UPDATE marketplace_offers
       SET status = 'declined', updated_at = NEW.updated_at
     WHERE order_id = NEW.id AND status = 'pending';
  END IF;
  RETURN NEW;
END;"""
        trigger = f"CREATE TRIGGER {qtrigger} AFTER UPDATE OF status ON {qtable} FOR EACH ROW WHEN (OLD.status = 'active' AND NEW.status = 'cancelled') EXECUTE FUNCTION {function}()"
        return [prefix + body + suffix, trigger]
    if name == "verified_assets_metadata_immutable" and table == "verified_assets":
        body = """BEGIN
  IF NOT (
    NEW.status = 'verified'
    AND NEW.owner_telegram_id IS DISTINCT FROM OLD.owner_telegram_id
    AND NEW.source IS NOT DISTINCT FROM OLD.source
    AND NEW.external_asset_id IS NOT DISTINCT FROM OLD.external_asset_id
  ) THEN
    RAISE EXCEPTION 'verified asset metadata is immutable';
  END IF;
  RETURN NEW;
END;"""
        trigger = (
            f"CREATE TRIGGER {qtrigger} BEFORE UPDATE OF source, external_asset_id, owner_telegram_id, "
            "title, asset_type, image_url, verification_method, verification_reference_hash, verified_at "
            f"ON {qtable} FOR EACH ROW EXECUTE FUNCTION {function}()"
        )
        return [prefix + body + suffix, trigger]
    return None

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
            translated_default = _translate_default(candidate["sqlite_default_expression"], candidate["postgresql_type_candidate"])
            if candidate["sqlite_default_expression"] is not None:
                if translated_default is None:
                    blockers.append({"table": table_name, "feature": f"default on {column_name}", "reason": f"SQLite default {candidate['sqlite_default_expression']!r} is not safely translatable."})
                else:
                    definition += f" DEFAULT {translated_default}"
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
            if index.get("partial") and not _portable_partial_index(str(sql)):
                blockers.append({"table": table_name, "feature": f"partial index {index.get('name')}", "reason": "Index predicate/expression is outside the conservative PostgreSQL-compatible subset."})

    for item in manifest.get("objects", []):
        if item.get("type") == "trigger":
            translated = _trigger_ddl(item)
            if translated is None:
                blockers.append({"table": str(item.get("table", "")), "feature": f"trigger {item.get('name')}", "reason": "No reviewed PostgreSQL trigger translation is available."})
            else:
                statements.extend(translated)
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
        "trigger_translation_count": sum(1 for item in manifest.get("objects", []) if item.get("type") == "trigger") - sum(1 for item in blockers if item["feature"].startswith("trigger ")),
        "blocker_count": len(blockers),
        "blockers": blockers,
        "execution_enabled": False,
        "ready_for_cutover": False,
        "scope_note": "Candidate DDL only. Unsupported defaults, triggers, views, indexes, and column features remain explicit blockers; nothing is executed.",
    }
