"""PostgreSQL trigger equivalents for marketplace offer/deal side effects.

This module is deliberately separate from the SQLite schema bootstrap. It can
be installed only after the canonical marketplace tables exist in the current
PostgreSQL search_path. It does not enable PostgreSQL runtime selection.
"""
from __future__ import annotations

from typing import Any


_FUNCTION_DDL = (
    """
    CREATE OR REPLACE FUNCTION hassle_nft_reject_immutable_event_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE EXCEPTION '% are immutable', TG_TABLE_NAME;
    END;
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION hassle_nft_offer_received_notification()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        INSERT INTO marketplace_notifications
            (id, user_id, event_type, offer_id, order_id, order_title, actor_name, created_at)
        SELECT 'nt-' || substr(md5(random()::text || clock_timestamp()::text || NEW.id), 1, 16),
               o.user_id, 'offer_received', NEW.id, NEW.order_id, o.title,
               NEW.sender_name, NEW.created_at
        FROM marketplace_orders o
        WHERE o.id = NEW.order_id AND o.user_id <> NEW.sender_user_id;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION hassle_nft_offer_status_notification()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF OLD.status = 'pending' AND NEW.status <> OLD.status
           AND NEW.status IN ('accepted', 'declined', 'withdrawn') THEN
            INSERT INTO marketplace_notifications
                (id, user_id, event_type, offer_id, order_id, order_title, actor_name, created_at)
            SELECT 'nt-' || substr(md5(random()::text || clock_timestamp()::text || NEW.id), 1, 16),
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
    END;
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION hassle_nft_order_cancel_declines_offers()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF OLD.status = 'active' AND NEW.status = 'cancelled' THEN
            UPDATE marketplace_offers
               SET status = 'declined', updated_at = NEW.updated_at
             WHERE order_id = NEW.id AND status = 'pending';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
)

_TRIGGER_DDL = (
    ("trg_marketplace_deal_events_no_update",
     "CREATE TRIGGER trg_marketplace_deal_events_no_update BEFORE UPDATE ON marketplace_deal_events "
     "FOR EACH ROW EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_deal_events_no_delete",
     "CREATE TRIGGER trg_marketplace_deal_events_no_delete BEFORE DELETE ON marketplace_deal_events "
     "FOR EACH ROW EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_deal_lifecycle_events_no_update",
     "CREATE TRIGGER trg_marketplace_deal_lifecycle_events_no_update BEFORE UPDATE ON marketplace_deal_lifecycle_events "
     "FOR EACH ROW EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_deal_lifecycle_events_no_delete",
     "CREATE TRIGGER trg_marketplace_deal_lifecycle_events_no_delete BEFORE DELETE ON marketplace_deal_lifecycle_events "
     "FOR EACH ROW EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_offer_received",
     "CREATE TRIGGER trg_marketplace_offer_received AFTER INSERT ON marketplace_offers "
     "FOR EACH ROW EXECUTE FUNCTION hassle_nft_offer_received_notification()"),
    ("trg_marketplace_offer_status_notification",
     "CREATE TRIGGER trg_marketplace_offer_status_notification AFTER UPDATE OF status ON marketplace_offers "
     "FOR EACH ROW EXECUTE FUNCTION hassle_nft_offer_status_notification()"),
    ("trg_marketplace_order_cancel_declines_offers",
     "CREATE TRIGGER trg_marketplace_order_cancel_declines_offers AFTER UPDATE OF status ON marketplace_orders "
     "FOR EACH ROW EXECUTE FUNCTION hassle_nft_order_cancel_declines_offers()"),
)

_TRIGGER_TABLES = {
    "trg_marketplace_deal_events_no_update": "marketplace_deal_events",
    "trg_marketplace_deal_events_no_delete": "marketplace_deal_events",
    "trg_marketplace_deal_lifecycle_events_no_update": "marketplace_deal_lifecycle_events",
    "trg_marketplace_deal_lifecycle_events_no_delete": "marketplace_deal_lifecycle_events",
    "trg_marketplace_offer_received": "marketplace_offers",
    "trg_marketplace_offer_status_notification": "marketplace_offers",
    "trg_marketplace_order_cancel_declines_offers": "marketplace_orders",
}
_TRIGGER_FUNCTIONS = {
    name: statement.split("EXECUTE FUNCTION ", 1)[1].split("(", 1)[0]
    for name, statement in _TRIGGER_DDL
}
_FUNCTION_NAMES = {
    "hassle_nft_reject_immutable_event_mutation",
    "hassle_nft_offer_received_notification",
    "hassle_nft_offer_status_notification",
    "hassle_nft_order_cancel_declines_offers",
}


def _verify_postgres_offer_triggers(connection: Any) -> dict[str, Any]:
    """Verify trigger placement, function binding, and enabled state in this schema."""
    rows = connection.execute("""
        SELECT t.tgname, c.relname, p.proname, t.tgenabled
        FROM pg_catalog.pg_trigger AS t
        JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_proc AS p ON p.oid = t.tgfoid
        WHERE NOT t.tgisinternal AND n.nspname = current_schema()
    """).fetchall()
    actual = {
        str(row["tgname"]): (str(row["relname"]), str(row["proname"]), str(row["tgenabled"]))
        for row in rows
        if str(row["tgname"]) in _TRIGGER_TABLES
    }
    problems = []
    for name, expected_table in _TRIGGER_TABLES.items():
        observed = actual.get(name)
        expected_function = _TRIGGER_FUNCTIONS[name]
        if observed is None:
            problems.append(f"missing trigger {name}")
        elif observed != (expected_table, expected_function, "O"):
            problems.append(
                f"trigger {name} expected {(expected_table, expected_function, 'O')}, got {observed}"
            )

    function_rows = connection.execute("""
        SELECT p.proname
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema()
    """).fetchall()
    installed_functions = {str(row["proname"]) for row in function_rows}
    missing_functions = sorted(_FUNCTION_NAMES - installed_functions)
    if missing_functions:
        problems.append("missing functions: " + ", ".join(missing_functions))
    if problems:
        raise RuntimeError("PostgreSQL offer trigger verification failed: " + "; ".join(problems))
    return {
        "verified": True,
        "triggers_verified": len(_TRIGGER_TABLES),
        "functions_verified": len(_FUNCTION_NAMES),
    }


def install_postgres_offer_triggers(connection: Any) -> dict[str, Any]:
    """Install reviewed PostgreSQL equivalents using the current search_path.

    The caller owns the surrounding transaction. The function fails closed if
    the connection does not expose the staged DB-API execute boundary.
    """
    execute = getattr(connection, "execute", None)
    if not callable(execute):
        raise TypeError("A PostgreSQL-compatible execute connection is required")

    for statement in _FUNCTION_DDL:
        execute(statement)

    for name, statement in _TRIGGER_DDL:
        table = _TRIGGER_TABLES[name]
        execute(f'DROP TRIGGER IF EXISTS "{name}" ON "{table}"')
        execute(statement)

    verification = _verify_postgres_offer_triggers(connection)
    return {
        "functions_installed": len(_FUNCTION_DDL),
        "triggers_installed": len(_TRIGGER_DDL),
        "trigger_names": [name for name, _ in _TRIGGER_DDL],
        **verification,
    }
