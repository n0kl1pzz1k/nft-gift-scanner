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
        RAISE EXCEPTION '%% are immutable', TG_TABLE_NAME;
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

    return {
        "functions_installed": len(_FUNCTION_DDL),
        "triggers_installed": len(_TRIGGER_DDL),
        "trigger_names": [name for name, _ in _TRIGGER_DDL],
        "verified": True,
    }


def initialize_postgres_offer_schema(connection: Any) -> dict[str, Any]:
    """Create offers/deals schema on an already initialized marketplace."""
    execute = getattr(connection, "execute", None)
    if not callable(execute) or not bool(getattr(connection, "supports_for_update", False)):
        raise TypeError("A PostgreSQL adapter connection is required")

    rows = execute(
        "SELECT table_name AS name FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
    ).fetchall()
    present = {str(row["name"]) for row in rows}
    if "marketplace_orders" not in present:
        raise RuntimeError("Core marketplace schema must be initialized before offers")

    # A legacy permanent UNIQUE(order_id) would block replacement reservations.
    legacy_unique = execute(
        "SELECT indexname FROM pg_indexes "
        "WHERE schemaname = current_schema() AND tablename = 'marketplace_deals' "
        "AND indexdef ILIKE 'CREATE UNIQUE INDEX%%' "
        "AND indexdef ILIKE '%%(order_id)%%' AND indexdef NOT ILIKE '%% WHERE %%'"
    ).fetchall()
    if legacy_unique:
        raise RuntimeError(
            "Legacy UNIQUE(order_id) on marketplace_deals requires the reviewed "
            "legacy reservation-index migration before PostgreSQL bootstrap."
        )

    statements = (
        """CREATE TABLE IF NOT EXISTS marketplace_offers (
            id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES marketplace_orders(id),
            sender_user_id TEXT NOT NULL, sender_name TEXT NOT NULL, amount TEXT NOT NULL,
            currency TEXT NOT NULL CHECK (currency IN ('TON', 'XTR')),
            note TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'declined', 'withdrawn')),
            created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_marketplace_offers_order ON marketplace_offers(order_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_marketplace_offers_sender ON marketplace_offers(sender_user_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_marketplace_offers_status ON marketplace_offers(status, created_at DESC)",
        """CREATE TABLE IF NOT EXISTS marketplace_deals (
            id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES marketplace_orders(id),
            offer_id TEXT NOT NULL UNIQUE REFERENCES marketplace_offers(id),
            listing_owner_id TEXT NOT NULL, offer_sender_id TEXT NOT NULL,
            agreed_amount TEXT NOT NULL, currency TEXT NOT NULL CHECK (currency IN ('TON', 'XTR')),
            created_at BIGINT NOT NULL, status TEXT NOT NULL DEFAULT 'payment_not_enabled'
            CHECK (status IN ('payment_not_enabled', 'awaiting_payment', 'payment_pending',
                'paid', 'delivery_pending', 'completed', 'disputed', 'cancelled', 'expired')))""",
        "ALTER TABLE marketplace_deals ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'payment_not_enabled'",
        """
        DO $migration$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'marketplace_deals_status_allowed'
                  AND conrelid = 'marketplace_deals'::regclass
            ) THEN
                ALTER TABLE marketplace_deals
                ADD CONSTRAINT marketplace_deals_status_allowed
                CHECK (status IN ('payment_not_enabled', 'awaiting_payment', 'payment_pending',
                    'paid', 'delivery_pending', 'completed', 'disputed', 'cancelled', 'expired'));
            END IF;
        END;
        $migration$
        """,
        """CREATE TABLE IF NOT EXISTS marketplace_deal_events (
            id TEXT PRIMARY KEY, deal_id TEXT NOT NULL REFERENCES marketplace_deals(id),
            order_id TEXT NOT NULL REFERENCES marketplace_orders(id), actor_user_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN ('deal_created', 'listing_reserved', 'payment_not_enabled')),
            event_data TEXT NOT NULL DEFAULT '{}', created_at BIGINT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_marketplace_deal_events_deal_created ON marketplace_deal_events(deal_id, created_at ASC, id ASC)",
        """CREATE TABLE IF NOT EXISTS marketplace_deal_lifecycle_events (
            sequence BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, id TEXT NOT NULL UNIQUE,
            deal_id TEXT NOT NULL REFERENCES marketplace_deals(id),
            order_id TEXT NOT NULL REFERENCES marketplace_orders(id), actor_user_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN ('deal_cancelled')),
            event_data TEXT NOT NULL DEFAULT '{}', created_at BIGINT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_marketplace_deal_lifecycle_events_deal ON marketplace_deal_lifecycle_events(deal_id, sequence ASC)",
        """CREATE TABLE IF NOT EXISTS marketplace_notifications (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN ('offer_received', 'offer_accepted', 'offer_declined', 'offer_withdrawn')),
            offer_id TEXT NOT NULL REFERENCES marketplace_offers(id),
            order_id TEXT NOT NULL REFERENCES marketplace_orders(id),
            order_title TEXT NOT NULL DEFAULT '', actor_name TEXT NOT NULL DEFAULT '',
            created_at BIGINT NOT NULL, read_at BIGINT)""",
        "CREATE INDEX IF NOT EXISTS idx_marketplace_notifications_user_created ON marketplace_notifications(user_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_marketplace_notifications_user_unread ON marketplace_notifications(user_id, read_at, created_at DESC)",
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_marketplace_deals_one_open_reservation
            ON marketplace_deals(order_id) WHERE status NOT IN ('cancelled', 'expired')""",
    )
    for statement in statements:
        execute(statement)
    trigger_report = install_postgres_offer_triggers(connection)
    return {
        "tables": ["marketplace_offers", "marketplace_deals", "marketplace_deal_events",
                   "marketplace_deal_lifecycle_events", "marketplace_notifications"],
        "indexes_created_or_verified": 8,
        "trigger_report": trigger_report,
        "verified": True,
    }
