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
        RAISE EXCEPTION USING MESSAGE = TG_TABLE_NAME || ' are immutable';
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
    AS $hassle_order$
    BEGIN
        IF OLD.status = 'active' AND NEW.status = 'cancelled' THEN
            UPDATE marketplace_offers
               SET status = 'declined', updated_at = NEW.updated_at
             WHERE order_id = NEW.id AND status = 'pending';
        END IF;
        RETURN NEW;
    END;
    $hassle_order$
    """,
    """
    CREATE OR REPLACE FUNCTION hassle_nft_assert_ledger_transaction_balanced()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $hassle_ledger$
    DECLARE
        v_transaction_id TEXT;
        v_expected_amount NUMERIC;
        v_currency TEXT;
        v_entry_count INTEGER;
        v_debit_count INTEGER;
        v_credit_count INTEGER;
        v_debit_total NUMERIC;
        v_credit_total NUMERIC;
        v_invalid_count INTEGER;
    BEGIN
        IF TG_TABLE_NAME = 'marketplace_ledger_transactions' THEN
            v_transaction_id := NEW.id;
        ELSE
            v_transaction_id := NEW.transaction_id;
        END IF;

        SELECT amount::numeric, currency
          INTO v_expected_amount, v_currency
          FROM marketplace_ledger_transactions
         WHERE id = v_transaction_id;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;

        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE direction = 'debit'),
               COUNT(*) FILTER (WHERE direction = 'credit'),
               COALESCE(SUM(CASE WHEN direction = 'debit' THEN amount::numeric ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN direction = 'credit' THEN amount::numeric ELSE 0 END), 0),
               COUNT(*) FILTER (WHERE currency <> v_currency OR amount::numeric <= 0)
          INTO v_entry_count, v_debit_count, v_credit_count,
               v_debit_total, v_credit_total, v_invalid_count
          FROM marketplace_ledger_entries
         WHERE transaction_id = v_transaction_id;

        IF v_entry_count <> 2
           OR v_debit_count <> 1
           OR v_credit_count <> 1
           OR v_invalid_count <> 0
           OR v_debit_total <> v_credit_total
           OR v_debit_total <> v_expected_amount THEN
            RAISE EXCEPTION USING MESSAGE =
                'ledger transaction must contain exactly one balanced debit and credit matching its amount';
        END IF;
        RETURN NULL;
    END;
    $hassle_ledger$
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
    ("trg_marketplace_payment_events_no_update",
     "CREATE TRIGGER trg_marketplace_payment_events_no_update BEFORE UPDATE ON marketplace_payment_events "
     "FOR EACH ROW EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_payment_events_no_delete",
     "CREATE TRIGGER trg_marketplace_payment_events_no_delete BEFORE DELETE ON marketplace_payment_events "
     "FOR EACH ROW EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_ledger_transactions_no_update",
     "CREATE TRIGGER trg_marketplace_ledger_transactions_no_update BEFORE UPDATE ON marketplace_ledger_transactions "
     "FOR EACH ROW EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_ledger_transactions_no_delete",
     "CREATE TRIGGER trg_marketplace_ledger_transactions_no_delete BEFORE DELETE ON marketplace_ledger_transactions "
     "FOR EACH ROW EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_ledger_entries_no_update",
     "CREATE TRIGGER trg_marketplace_ledger_entries_no_update BEFORE UPDATE ON marketplace_ledger_entries "
     "FOR EACH ROW EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_ledger_entries_no_delete",
     "CREATE TRIGGER trg_marketplace_ledger_entries_no_delete BEFORE DELETE ON marketplace_ledger_entries "
     "FOR EACH ROW EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_deal_events_no_truncate",
     "CREATE TRIGGER trg_marketplace_deal_events_no_truncate BEFORE TRUNCATE ON marketplace_deal_events "
     "FOR EACH STATEMENT EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_deal_lifecycle_events_no_truncate",
     "CREATE TRIGGER trg_marketplace_deal_lifecycle_events_no_truncate BEFORE TRUNCATE ON marketplace_deal_lifecycle_events "
     "FOR EACH STATEMENT EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_payment_events_no_truncate",
     "CREATE TRIGGER trg_marketplace_payment_events_no_truncate BEFORE TRUNCATE ON marketplace_payment_events "
     "FOR EACH STATEMENT EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_ledger_transactions_no_truncate",
     "CREATE TRIGGER trg_marketplace_ledger_transactions_no_truncate BEFORE TRUNCATE ON marketplace_ledger_transactions "
     "FOR EACH STATEMENT EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_ledger_entries_no_truncate",
     "CREATE TRIGGER trg_marketplace_ledger_entries_no_truncate BEFORE TRUNCATE ON marketplace_ledger_entries "
     "FOR EACH STATEMENT EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_escrow_events_no_update",
     "CREATE TRIGGER trg_marketplace_escrow_events_no_update BEFORE UPDATE ON marketplace_escrow_events "
     "FOR EACH ROW EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_escrow_events_no_delete",
     "CREATE TRIGGER trg_marketplace_escrow_events_no_delete BEFORE DELETE ON marketplace_escrow_events "
     "FOR EACH ROW EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_escrow_events_no_truncate",
     "CREATE TRIGGER trg_marketplace_escrow_events_no_truncate BEFORE TRUNCATE ON marketplace_escrow_events "
     "FOR EACH STATEMENT EXECUTE FUNCTION hassle_nft_reject_immutable_event_mutation()"),
    ("trg_marketplace_ledger_transaction_balanced",
     "CREATE CONSTRAINT TRIGGER trg_marketplace_ledger_transaction_balanced AFTER INSERT ON marketplace_ledger_transactions "
     "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION hassle_nft_assert_ledger_transaction_balanced()"),
    ("trg_marketplace_ledger_entries_balanced",
     "CREATE CONSTRAINT TRIGGER trg_marketplace_ledger_entries_balanced AFTER INSERT ON marketplace_ledger_entries "
     "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION hassle_nft_assert_ledger_transaction_balanced()"),
)

_TRIGGER_TABLES = {
    "trg_marketplace_deal_events_no_update": "marketplace_deal_events",
    "trg_marketplace_deal_events_no_delete": "marketplace_deal_events",
    "trg_marketplace_deal_lifecycle_events_no_update": "marketplace_deal_lifecycle_events",
    "trg_marketplace_deal_lifecycle_events_no_delete": "marketplace_deal_lifecycle_events",
    "trg_marketplace_offer_received": "marketplace_offers",
    "trg_marketplace_offer_status_notification": "marketplace_offers",
    "trg_marketplace_order_cancel_declines_offers": "marketplace_orders",
    "trg_marketplace_payment_events_no_update": "marketplace_payment_events",
    "trg_marketplace_payment_events_no_delete": "marketplace_payment_events",
    "trg_marketplace_ledger_transactions_no_update": "marketplace_ledger_transactions",
    "trg_marketplace_ledger_transactions_no_delete": "marketplace_ledger_transactions",
    "trg_marketplace_ledger_entries_no_update": "marketplace_ledger_entries",
    "trg_marketplace_ledger_entries_no_delete": "marketplace_ledger_entries",
    "trg_marketplace_escrow_events_no_update": "marketplace_escrow_events",
    "trg_marketplace_escrow_events_no_delete": "marketplace_escrow_events",
    "trg_marketplace_escrow_events_no_truncate": "marketplace_escrow_events",
    "trg_marketplace_deal_events_no_truncate": "marketplace_deal_events",
    "trg_marketplace_deal_lifecycle_events_no_truncate": "marketplace_deal_lifecycle_events",
    "trg_marketplace_payment_events_no_truncate": "marketplace_payment_events",
    "trg_marketplace_ledger_transactions_no_truncate": "marketplace_ledger_transactions",
    "trg_marketplace_ledger_entries_no_truncate": "marketplace_ledger_entries",
    "trg_marketplace_ledger_transaction_balanced": "marketplace_ledger_transactions",
    "trg_marketplace_ledger_entries_balanced": "marketplace_ledger_entries",
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
    "hassle_nft_assert_ledger_transaction_balanced",
}

def _verify_postgres_offer_triggers(connection: Any) -> dict[str, Any]:
    """Verify trigger placement, function binding, and enabled state in this schema."""
    def value(row: Any, key: str, index: int) -> Any:
        try:
            return row[key]
        except (KeyError, TypeError):
            return row[index]

    rows = connection.execute("""
        SELECT t.tgname, c.relname, p.proname, t.tgenabled, t.tgdeferrable, t.tginitdeferred
        FROM pg_catalog.pg_trigger AS t
        JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_proc AS p ON p.oid = t.tgfoid
        WHERE NOT t.tgisinternal AND n.nspname = current_schema()
    """).fetchall()
    actual = {
        str(value(row, "tgname", 0)): (
            str(value(row, "relname", 1)),
            str(value(row, "proname", 2)),
            str(value(row, "tgenabled", 3)),
            bool(value(row, "tgdeferrable", 4)),
            bool(value(row, "tginitdeferred", 5)),
        )
        for row in rows
        if str(value(row, "tgname", 0)) in _TRIGGER_TABLES
    }
    problems = []
    for name, expected_table in _TRIGGER_TABLES.items():
        observed = actual.get(name)
        expected_function = _TRIGGER_FUNCTIONS[name]
        if observed is None:
            problems.append(f"missing trigger {name}")
        else:
            deferred = name in {
                "trg_marketplace_ledger_transaction_balanced",
                "trg_marketplace_ledger_entries_balanced",
            }
            expected = (expected_table, expected_function, "O", deferred, deferred)
            if observed != expected:
                problems.append(f"trigger {name} expected {expected}, got {observed}")

    function_rows = connection.execute("""
        SELECT p.proname
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema()
    """).fetchall()
    installed_functions = {str(value(row, "proname", 0)) for row in function_rows}
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


def verify_postgres_offer_schema(connection: Any) -> dict[str, Any]:
    """Verify the complete offers/deals table and index contract."""
    expected_columns = {
        "marketplace_offers": {
            "id", "order_id", "sender_user_id", "sender_name", "amount",
            "currency", "note", "status", "created_at", "updated_at",
        },
        "marketplace_deals": {
            "id", "order_id", "offer_id", "listing_owner_id", "offer_sender_id",
            "agreed_amount", "currency", "created_at", "status",
        },
        "marketplace_deal_events": {
            "id", "deal_id", "order_id", "actor_user_id", "event_type",
            "event_data", "created_at",
        },
        "marketplace_deal_lifecycle_events": {
            "sequence", "id", "deal_id", "order_id", "actor_user_id",
            "event_type", "event_data", "created_at",
        },
        "marketplace_notifications": {
            "id", "user_id", "event_type", "offer_id", "order_id",
            "order_title", "actor_name", "created_at", "read_at",
        },
        "marketplace_payment_intents": {
            "id", "deal_id", "provider", "currency", "amount", "status",
            "idempotency_key", "provider_reference", "invoice_payload",
            "expires_at", "created_at", "updated_at", "confirmed_at", "metadata_json",
        },
        "marketplace_payment_events": {
            "id", "provider", "provider_event_id", "payment_intent_id",
            "event_type", "payload_hash", "created_at",
        },
        "marketplace_ledger_transactions": {
            "id", "idempotency_key", "reference_type", "reference_id",
            "currency", "amount", "created_at",
        },
        "marketplace_ledger_entries": {
            "id", "transaction_id", "account_type", "account_id",
            "direction", "currency", "amount", "created_at",
        },
        "marketplace_escrow_events": {
            "id", "deal_id", "actor_user_id", "event_type", "event_key",
            "event_data", "created_at",
        },
    }        "marketplace_ledger_entries": {
            "id", "transaction_id", "account_type", "account_id",
            "direction", "currency", "amount", "created_at",
        },
    }
    expected_indexes = {
        "idx_marketplace_offers_order",
        "idx_marketplace_offers_sender",
        "idx_marketplace_offers_status",
        "idx_marketplace_deal_events_deal_created",
        "idx_marketplace_deal_lifecycle_events_deal",
        "idx_marketplace_notifications_user_created",
        "idx_marketplace_notifications_user_unread",
        "idx_marketplace_deals_one_open_reservation",
        "idx_marketplace_payment_intents_deal",
        "idx_marketplace_payment_intents_status",
        "idx_marketplace_payment_intents_one_open_per_deal",
        "idx_marketplace_payment_intents_idempotency_key",
        "idx_marketplace_payment_intents_provider_reference",
        "idx_marketplace_payment_intents_invoice_payload",
        "idx_marketplace_payment_events_provider_event",
        "idx_marketplace_payment_events_intent",
        "idx_marketplace_ledger_transactions_idempotency_key",
        "idx_marketplace_ledger_transactions_reference",
        "idx_marketplace_ledger_entries_transaction",
        "idx_marketplace_ledger_entries_unique_account_direction",
        "idx_marketplace_escrow_events_deal_created",
        "idx_marketplace_escrow_events_event_key",
    }        "idx_marketplace_ledger_entries_unique_account_direction",
    }
    table_rows = connection.execute(
        "SELECT table_name AS name FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
    ).fetchall()
    present = {str(row["name"]) for row in table_rows}
    column_rows = connection.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema()"
    ).fetchall()
    actual_columns: dict[str, set[str]] = {}
    for row in column_rows:
        actual_columns.setdefault(str(row["table_name"]), set()).add(str(row["column_name"]))
    index_rows = connection.execute(
        "SELECT indexname AS name FROM pg_indexes WHERE schemaname = current_schema()"
    ).fetchall()
    present_indexes = {str(row["name"]) for row in index_rows}

    problems: list[str] = []
    for table_name, expected in expected_columns.items():
        if table_name not in present:
            problems.append(f"missing table: {table_name}")
            continue
        actual = actual_columns.get(table_name, set())
        missing = expected - actual
        unexpected = actual - expected
        if missing:
            problems.append(f"missing columns in {table_name}: " + ", ".join(sorted(missing)))
        if unexpected:
            problems.append(f"unexpected columns in {table_name}: " + ", ".join(sorted(unexpected)))
    missing_indexes = expected_indexes - present_indexes
    if missing_indexes:
        problems.append("missing indexes: " + ", ".join(sorted(missing_indexes)))
    if problems:
        raise RuntimeError("PostgreSQL offer schema verification failed: " + "; ".join(problems))
    trigger_report = _verify_postgres_offer_triggers(connection)
    return {
        "verified": True,
        "tables_verified": len(expected_columns),
        "columns_verified": sum(len(columns) for columns in expected_columns.values()),
        "indexes_verified": len(expected_indexes),
        **trigger_report,
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
        """CREATE TABLE IF NOT EXISTS marketplace_payment_intents (
            id TEXT PRIMARY KEY, deal_id TEXT NOT NULL REFERENCES marketplace_deals(id),
            provider TEXT NOT NULL CHECK (provider IN ('telegram_stars', 'ton')),
            currency TEXT NOT NULL CHECK (currency IN ('XTR', 'TON')),
            amount TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'created'
                CHECK (status IN ('created', 'pending', 'confirmed', 'failed', 'expired', 'refunded')),
            idempotency_key TEXT NOT NULL, provider_reference TEXT, invoice_payload TEXT,
            expires_at BIGINT NOT NULL, created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL,
            confirmed_at BIGINT, metadata_json TEXT NOT NULL DEFAULT '{}',
            CHECK (expires_at > created_at))""",
        "CREATE INDEX IF NOT EXISTS idx_marketplace_payment_intents_deal ON marketplace_payment_intents(deal_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_marketplace_payment_intents_status ON marketplace_payment_intents(status, expires_at)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_marketplace_payment_intents_one_open_per_deal ON marketplace_payment_intents(deal_id) WHERE status IN ('created', 'pending')",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_marketplace_payment_intents_idempotency_key ON marketplace_payment_intents(idempotency_key)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_marketplace_payment_intents_provider_reference ON marketplace_payment_intents(provider_reference)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_marketplace_payment_intents_invoice_payload ON marketplace_payment_intents(invoice_payload)",
        """CREATE TABLE IF NOT EXISTS marketplace_payment_events (
            id TEXT PRIMARY KEY, provider TEXT NOT NULL CHECK (provider IN ('telegram_stars', 'ton')),
            provider_event_id TEXT NOT NULL, payment_intent_id TEXT NOT NULL REFERENCES marketplace_payment_intents(id),
            event_type TEXT NOT NULL, payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
            created_at BIGINT NOT NULL)""",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_marketplace_payment_events_provider_event ON marketplace_payment_events(provider, provider_event_id)",
        "CREATE INDEX IF NOT EXISTS idx_marketplace_payment_events_intent ON marketplace_payment_events(payment_intent_id, created_at ASC)",
        """CREATE TABLE IF NOT EXISTS marketplace_escrow_events (
             id TEXT PRIMARY KEY, deal_id TEXT NOT NULL REFERENCES marketplace_deals(id),
             actor_user_id TEXT NOT NULL,
             event_type TEXT NOT NULL CHECK (event_type IN (
                 'deal_payment_requested', 'payment_intent_created', 'payment_pending',
                 'payment_confirmed', 'escrow_funded', 'asset_transfer_requested',
                 'asset_transfer_submitted', 'asset_transfer_confirmed', 'deal_completed',
                 'deal_disputed', 'refund_requested', 'refund_confirmed', 'payout_released',
                 'deal_cancelled', 'deal_expired')),
             event_key TEXT NOT NULL, event_data TEXT NOT NULL DEFAULT '{}',
             created_at BIGINT NOT NULL)""",
         "CREATE UNIQUE INDEX IF NOT EXISTS idx_marketplace_escrow_events_event_key ON marketplace_escrow_events(event_key)",
         "CREATE INDEX IF NOT EXISTS idx_marketplace_escrow_events_deal_created ON marketplace_escrow_events(deal_id, created_at ASC, id ASC)",
         """CREATE TABLE IF NOT EXISTS marketplace_ledger_transactions (
            id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL, reference_type TEXT NOT NULL
                CHECK (reference_type IN ('payment_intent', 'deal', 'refund', 'withdrawal', 'fee')),
            reference_id TEXT NOT NULL, currency TEXT NOT NULL CHECK (currency IN ('XTR', 'TON')),
            amount TEXT NOT NULL, created_at BIGINT NOT NULL)""",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_marketplace_ledger_transactions_idempotency_key ON marketplace_ledger_transactions(idempotency_key)",
        "CREATE INDEX IF NOT EXISTS idx_marketplace_ledger_transactions_reference ON marketplace_ledger_transactions(reference_type, reference_id, created_at ASC)",
        """CREATE TABLE IF NOT EXISTS marketplace_ledger_entries (
            id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL REFERENCES marketplace_ledger_transactions(id),
            account_type TEXT NOT NULL, account_id TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
            currency TEXT NOT NULL CHECK (currency IN ('XTR', 'TON')),
            amount TEXT NOT NULL, created_at BIGINT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_marketplace_ledger_entries_transaction ON marketplace_ledger_entries(transaction_id, direction)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_marketplace_ledger_entries_unique_account_direction ON marketplace_ledger_entries(transaction_id, account_type, account_id, direction)",
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_marketplace_deals_one_open_reservation
            ON marketplace_deals(order_id) WHERE status NOT IN ('cancelled', 'expired')""",
    )
    for statement in statements:
        execute(statement)
    install_postgres_offer_triggers(connection)
    verification = verify_postgres_offer_schema(connection)
    return {
        "tables": ["marketplace_offers", "marketplace_deals", "marketplace_deal_events",
                   "marketplace_deal_lifecycle_events", "marketplace_notifications", "marketplace_payment_intents",
                    "marketplace_payment_events", "marketplace_ledger_transactions", "marketplace_ledger_entries",
                    "marketplace_escrow_events"],"marketplace_payment_events", "marketplace_ledger_transactions", "marketplace_ledger_entries"],
        "indexes_created_or_verified": verification["indexes_verified"],
        "verification": verification,
        "verified": verification["verified"],
    }
