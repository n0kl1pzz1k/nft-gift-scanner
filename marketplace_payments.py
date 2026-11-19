"""Disabled-by-default payment persistence primitives for HASSLE NFT.

This module records intents, provider events and balanced accounting entries.
It deliberately does not create invoices, submit TON transactions, or enable
deal acceptance. Provider integrations must call these primitives only after
the escrow and asset-delivery state machine is ready.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

PAYMENT_PROVIDERS = {"telegram_stars", "ton"}
PAYMENT_CURRENCIES = {"XTR", "TON"}
PAYMENT_INTENT_STATES = {"created", "pending", "confirmed", "failed", "expired", "refunded"}
_LEDGER_REFERENCE_TYPES = {"payment_intent", "deal", "refund", "withdrawal", "fee"}
_ALLOWED_TRANSITIONS = {
    "created": {"pending", "failed", "expired"},
    "pending": {"confirmed", "failed", "expired"},
    "confirmed": {"refunded"},
}


class PaymentConflict(ValueError):
    """An idempotency key or provider event was reused with different data."""


class PaymentStateError(ValueError):
    """A payment operation is invalid for the current deal or intent state."""


def initialize_sqlite_payment_schema(conn: Any) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS marketplace_payment_intents (
        id TEXT PRIMARY KEY,
        deal_id TEXT NOT NULL,
        provider TEXT NOT NULL CHECK(provider IN ('telegram_stars', 'ton')),
        currency TEXT NOT NULL CHECK(currency IN ('XTR', 'TON')),
        amount TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'created'
            CHECK(status IN ('created', 'pending', 'confirmed', 'failed', 'expired', 'refunded')),
        idempotency_key TEXT NOT NULL UNIQUE,
        provider_reference TEXT UNIQUE,
        invoice_payload TEXT UNIQUE,
        expires_at INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        confirmed_at INTEGER,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        CHECK(expires_at > created_at),
        FOREIGN KEY(deal_id) REFERENCES marketplace_deals(id)
    );
    CREATE INDEX IF NOT EXISTS idx_marketplace_payment_intents_deal
        ON marketplace_payment_intents(deal_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_marketplace_payment_intents_status
        ON marketplace_payment_intents(status, expires_at);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_marketplace_payment_intents_one_open_per_deal
        ON marketplace_payment_intents(deal_id)
        WHERE status IN ('created', 'pending');

    CREATE TABLE IF NOT EXISTS marketplace_payment_events (
        id TEXT PRIMARY KEY,
        provider TEXT NOT NULL CHECK(provider IN ('telegram_stars', 'ton')),
        provider_event_id TEXT NOT NULL,
        payment_intent_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64),
        created_at INTEGER NOT NULL,
        UNIQUE(provider, provider_event_id),
        FOREIGN KEY(payment_intent_id) REFERENCES marketplace_payment_intents(id)
    );
    CREATE INDEX IF NOT EXISTS idx_marketplace_payment_events_intent
        ON marketplace_payment_events(payment_intent_id, created_at ASC);

    CREATE TABLE IF NOT EXISTS marketplace_ledger_transactions (
        id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        reference_type TEXT NOT NULL
            CHECK(reference_type IN ('payment_intent', 'deal', 'refund', 'withdrawal', 'fee')),
        reference_id TEXT NOT NULL,
        currency TEXT NOT NULL CHECK(currency IN ('XTR', 'TON')),
        amount TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_marketplace_ledger_transactions_reference
        ON marketplace_ledger_transactions(reference_type, reference_id, created_at ASC);

    CREATE TABLE IF NOT EXISTS marketplace_escrow_events (
        id TEXT PRIMARY KEY,
        deal_id TEXT NOT NULL,
        actor_user_id TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK(event_type IN (
            'deal_payment_requested', 'payment_intent_created', 'payment_pending',
            'payment_confirmed', 'escrow_funded', 'asset_transfer_requested',
            'asset_transfer_submitted', 'asset_transfer_confirmed', 'deal_completed',
            'deal_disputed', 'refund_requested', 'refund_confirmed', 'payout_released',
            'deal_cancelled', 'deal_expired', 'dispute_resolution_approved',
            'dispute_resolution_ready'
        )),
        event_key TEXT NOT NULL UNIQUE,
        event_data TEXT NOT NULL DEFAULT '{}',
        created_at INTEGER NOT NULL,
        FOREIGN KEY(deal_id) REFERENCES marketplace_deals(id)
    );
    CREATE INDEX IF NOT EXISTS idx_marketplace_escrow_events_deal_created
        ON marketplace_escrow_events(deal_id, created_at ASC, id ASC);
    CREATE TRIGGER IF NOT EXISTS trg_marketplace_escrow_events_no_update
    BEFORE UPDATE ON marketplace_escrow_events
    BEGIN
        SELECT RAISE(ABORT, 'escrow events are immutable');
    END;
    CREATE TRIGGER IF NOT EXISTS trg_marketplace_escrow_events_no_delete
    BEFORE DELETE ON marketplace_escrow_events
    BEGIN
        SELECT RAISE(ABORT, 'escrow events are immutable');
    END;

    CREATE TABLE IF NOT EXISTS marketplace_dispute_resolution_approvals (
        id TEXT PRIMARY KEY,
        deal_id TEXT NOT NULL,
        admin_user_id TEXT NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN ('refund_buyer', 'release_to_seller')),
        idempotency_key TEXT NOT NULL UNIQUE,
        created_at INTEGER NOT NULL,
        UNIQUE(deal_id, admin_user_id),
        FOREIGN KEY(deal_id) REFERENCES marketplace_deals(id)
    );
    CREATE INDEX IF NOT EXISTS idx_marketplace_dispute_approvals_deal
        ON marketplace_dispute_resolution_approvals(deal_id, created_at ASC);
    CREATE TRIGGER IF NOT EXISTS trg_marketplace_dispute_approvals_no_update
    BEFORE UPDATE ON marketplace_dispute_resolution_approvals
    BEGIN
        SELECT RAISE(ABORT, 'dispute approvals are immutable');
    END;
    CREATE TRIGGER IF NOT EXISTS trg_marketplace_dispute_approvals_no_delete
    BEFORE DELETE ON marketplace_dispute_resolution_approvals
    BEGIN
        SELECT RAISE(ABORT, 'dispute approvals are immutable');
    END;

    CREATE TABLE IF NOT EXISTS marketplace_ledger_entries (
        id TEXT PRIMARY KEY,
        transaction_id TEXT NOT NULL,
        account_type TEXT NOT NULL,
        account_id TEXT NOT NULL,
        direction TEXT NOT NULL CHECK(direction IN ('debit', 'credit')),
        currency TEXT NOT NULL CHECK(currency IN ('XTR', 'TON')),
        amount TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        FOREIGN KEY(transaction_id) REFERENCES marketplace_ledger_transactions(id),
        UNIQUE(transaction_id, account_type, account_id, direction)
    );
    CREATE INDEX IF NOT EXISTS idx_marketplace_ledger_entries_transaction
        ON marketplace_ledger_entries(transaction_id, direction);
    CREATE TRIGGER IF NOT EXISTS trg_marketplace_payment_events_no_update
    BEFORE UPDATE ON marketplace_payment_events
    BEGIN
        SELECT RAISE(ABORT, 'payment events are immutable');
    END;
    CREATE TRIGGER IF NOT EXISTS trg_marketplace_payment_events_no_delete
    BEFORE DELETE ON marketplace_payment_events
    BEGIN
        SELECT RAISE(ABORT, 'payment events are immutable');
    END;
    CREATE TRIGGER IF NOT EXISTS trg_marketplace_ledger_transactions_no_update
    BEFORE UPDATE ON marketplace_ledger_transactions
    BEGIN
        SELECT RAISE(ABORT, 'ledger transactions are immutable');
    END;
    CREATE TRIGGER IF NOT EXISTS trg_marketplace_ledger_transactions_no_delete
    BEFORE DELETE ON marketplace_ledger_transactions
    BEGIN
        SELECT RAISE(ABORT, 'ledger transactions are immutable');
    END;
    CREATE TRIGGER IF NOT EXISTS trg_marketplace_ledger_entries_no_update
    BEFORE UPDATE ON marketplace_ledger_entries
    BEGIN
        SELECT RAISE(ABORT, 'ledger entries are immutable');
    END;
    CREATE TRIGGER IF NOT EXISTS trg_marketplace_ledger_entries_no_delete
    BEFORE DELETE ON marketplace_ledger_entries
    BEGIN
        SELECT RAISE(ABORT, 'ledger entries are immutable');
    END;
    """)


def _amount_text(value: Any, currency: str) -> str:
    if currency not in PAYMENT_CURRENCIES:
        raise ValueError("Unsupported payment currency")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Payment amount must be a decimal number") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Payment amount must be greater than zero")
    normalized = amount.normalize()
    decimals = max(0, -normalized.as_tuple().exponent)
    if currency == "XTR" and normalized != normalized.to_integral_value():
        raise ValueError("Telegram Stars amounts must be whole numbers")
    if currency == "TON" and decimals > 9:
        raise ValueError("TON amounts cannot exceed 9 decimal places")
    return format(normalized, "f")


def _intent_dict(row: Any) -> dict[str, Any]:
    if row is None:
        raise PaymentStateError("Payment intent not found")
    return dict(row)


def create_payment_intent(
    conn: Any,
    *,
    deal_id: str,
    provider: str,
    currency: str,
    amount: Any,
    idempotency_key: str,
    expires_at: int,
    metadata: dict[str, Any] | None = None,
    invoice_payload: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    if provider not in PAYMENT_PROVIDERS:
        raise ValueError("Unsupported payment provider")
    if currency not in PAYMENT_CURRENCIES:
        raise ValueError("Unsupported payment currency")
    expected_currency = {"telegram_stars": "XTR", "ton": "TON"}[provider]
    if currency != expected_currency:
        raise ValueError("Payment provider and currency do not match")
    amount_value = _amount_text(amount, currency)
    key = str(idempotency_key or "").strip()
    if not key or len(key) > 128:
        raise ValueError("A 1-128 character idempotency key is required")
    deal_id = str(deal_id or "").strip()
    if not deal_id:
        raise ValueError("Deal ID is required")
    timestamp = int(now if now is not None else __import__("time").time())
    expiry = int(expires_at)
    if expiry <= timestamp:
        raise ValueError("Payment intent expiry must be in the future")
    metadata_json = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))
    payload_value = str(invoice_payload or "").strip() or None
    if payload_value is not None and len(payload_value.encode("utf-8")) > 128:
        raise ValueError("Invoice payload must contain 1-128 UTF-8 bytes")
    existing = conn.execute(
        "SELECT * FROM marketplace_payment_intents WHERE idempotency_key = ?",
        (key,),
    ).fetchone()
    if existing is not None:
        row = _intent_dict(existing)
        expected = (deal_id, provider, currency, amount_value, expiry, metadata_json, payload_value)
        actual = (
            row["deal_id"], row["provider"], row["currency"], row["amount"],
            row["expires_at"], row["metadata_json"], row["invoice_payload"],
        )
        if actual != expected:
            raise PaymentConflict("Payment idempotency key was reused with different parameters")
        return row

    deal = conn.execute("SELECT status FROM marketplace_deals WHERE id = ?", (deal_id,)).fetchone()
    if deal is None:
        raise PaymentStateError("Deal not found")
    if deal["status"] != "awaiting_payment":
        raise PaymentStateError("A payment intent requires a deal awaiting payment")

    intent_id = "pi-" + uuid.uuid4().hex[:24]
    conn.execute(
        """INSERT INTO marketplace_payment_intents
           (id, deal_id, provider, currency, amount, status, idempotency_key,
            invoice_payload, expires_at, created_at, updated_at, metadata_json)
           VALUES (?, ?, ?, ?, ?, 'created', ?, ?, ?, ?, ?, ?)""",
        (intent_id, deal_id, provider, currency, amount_value, key, payload_value, expiry,
         timestamp, timestamp, metadata_json),
    )
    row = conn.execute(
        "SELECT * FROM marketplace_payment_intents WHERE id = ?", (intent_id,)
    ).fetchone()
    return _intent_dict(row)


def get_payment_intent(conn: Any, intent_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM marketplace_payment_intents WHERE id = ?",
        (str(intent_id or "").strip(),),
    ).fetchone()
    return _intent_dict(row)


def get_payment_intent_by_invoice_payload(conn: Any, invoice_payload: str) -> dict[str, Any]:
    payload = str(invoice_payload or "").strip()
    if not payload:
        raise PaymentStateError("Invoice payload is required")
    row = conn.execute(
        "SELECT * FROM marketplace_payment_intents WHERE invoice_payload = ?",
        (payload,),
    ).fetchone()
    return _intent_dict(row)


def transition_payment_intent(
    conn: Any,
    intent_id: str,
    new_status: str,
    *,
    provider_reference: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    if new_status not in PAYMENT_INTENT_STATES:
        raise ValueError("Unsupported payment intent status")
    row = conn.execute(
        "SELECT * FROM marketplace_payment_intents WHERE id = ?", (intent_id,)
    ).fetchone()
    if row is None:
        raise PaymentStateError("Payment intent not found")
    current = str(row["status"])
    if current == new_status:
        if provider_reference and row["provider_reference"] not in (None, provider_reference):
            raise PaymentConflict("Payment intent already has a different provider reference")
        return _intent_dict(row)
    if new_status not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise PaymentStateError(f"Invalid payment intent transition: {current} -> {new_status}")
    reference = str(provider_reference or row["provider_reference"] or "").strip() or None
    if new_status == "confirmed" and not reference:
        raise PaymentStateError("A confirmed payment requires a provider transaction reference")
    timestamp = int(now if now is not None else __import__("time").time())
    confirmed_at = timestamp if new_status == "confirmed" else row["confirmed_at"]
    changed = conn.execute(
        """UPDATE marketplace_payment_intents
           SET status = ?, provider_reference = ?, updated_at = ?, confirmed_at = ?
           WHERE id = ? AND status = ?""",
        (new_status, reference, timestamp, confirmed_at, intent_id, current),
    )
    if changed.rowcount != 1:
        raise PaymentConflict("Payment intent changed concurrently")
    return _intent_dict(conn.execute(
        "SELECT * FROM marketplace_payment_intents WHERE id = ?", (intent_id,)
    ).fetchone())


def record_payment_event(
    conn: Any,
    *,
    provider: str,
    provider_event_id: str,
    payment_intent_id: str,
    event_type: str,
    payload_hash: str,
    now: int | None = None,
) -> bool:
    if provider not in PAYMENT_PROVIDERS:
        raise ValueError("Unsupported payment provider")
    event_id = str(provider_event_id or "").strip()
    kind = str(event_type or "").strip()
    digest = str(payload_hash or "").lower()
    if not event_id or len(event_id) > 256 or not kind:
        raise ValueError("Provider event ID and event type are required")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("Provider payload hash must be a SHA-256 hex digest")
    intent = conn.execute(
        "SELECT id FROM marketplace_payment_intents WHERE id = ?",
        (payment_intent_id,),
    ).fetchone()
    if intent is None:
        raise PaymentStateError("Payment intent not found")
    existing = conn.execute(
        """SELECT * FROM marketplace_payment_events
           WHERE provider = ? AND provider_event_id = ?""",
        (provider, event_id),
    ).fetchone()
    if existing is not None:
        if (
            existing["payment_intent_id"] != payment_intent_id
            or existing["event_type"] != kind
            or existing["payload_hash"] != digest
        ):
            raise PaymentConflict("Provider event ID was reused with different data")
        return False
    timestamp = int(now if now is not None else __import__("time").time())
    conn.execute(
        """INSERT INTO marketplace_payment_events
           (id, provider, provider_event_id, payment_intent_id, event_type, payload_hash, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("pe-" + uuid.uuid4().hex[:24], provider, event_id, payment_intent_id, kind, digest, timestamp),
    )
    return True


def post_ledger_transaction(
    conn: Any,
    *,
    idempotency_key: str,
    reference_type: str,
    reference_id: str,
    currency: str,
    debit_account: tuple[str, str],
    credit_account: tuple[str, str],
    debit_amount: Any,
    credit_amount: Any,
    now: int | None = None,
) -> dict[str, Any]:
    key = str(idempotency_key or "").strip()
    reference = str(reference_id or "").strip()
    if not key or len(key) > 128 or not reference:
        raise ValueError("Ledger idempotency key and reference ID are required")
    if reference_type not in _LEDGER_REFERENCE_TYPES:
        raise ValueError("Unsupported ledger reference type")
    debit_value = _amount_text(debit_amount, currency)
    credit_value = _amount_text(credit_amount, currency)
    if debit_value != credit_value:
        raise ValueError("Double-entry ledger amounts must balance")
    if len(debit_account) != 2 or len(credit_account) != 2:
        raise ValueError("Ledger accounts must contain account type and account ID")
    debit_type, debit_id = (str(value).strip() for value in debit_account)
    credit_type, credit_id = (str(value).strip() for value in credit_account)
    if not all((debit_type, debit_id, credit_type, credit_id)):
        raise ValueError("Ledger account identifiers are required")
    if (debit_type, debit_id) == (credit_type, credit_id):
        raise ValueError("Debit and credit accounts must be different")

    existing = conn.execute(
        "SELECT * FROM marketplace_ledger_transactions WHERE idempotency_key = ?",
        (key,),
    ).fetchone()
    if existing is not None:
        row = dict(existing)
        expected = (reference_type, reference, currency, debit_value)
        actual = (row["reference_type"], row["reference_id"], row["currency"], row["amount"])
        entries = conn.execute(
            """SELECT account_type, account_id, direction, amount, currency
               FROM marketplace_ledger_entries WHERE transaction_id = ?
               ORDER BY direction""",
            (row["id"],),
        ).fetchall()
        expected_entries = {
            (debit_type, debit_id, "debit", debit_value, currency),
            (credit_type, credit_id, "credit", credit_value, currency),
        }
        actual_entries = {
            (entry["account_type"], entry["account_id"], entry["direction"], entry["amount"], entry["currency"])
            for entry in entries
        }
        if actual != expected or actual_entries != expected_entries:
            raise PaymentConflict("Ledger idempotency key was reused with different entries")
        return {"id": row["id"], "idempotent_replay": True, "entries": 2}

    timestamp = int(now if now is not None else __import__("time").time())
    transaction_id = "lt-" + uuid.uuid4().hex[:24]
    conn.execute("SAVEPOINT hassle_ledger_post")
    try:
        conn.execute(
            """INSERT INTO marketplace_ledger_transactions
               (id, idempotency_key, reference_type, reference_id, currency, amount, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (transaction_id, key, reference_type, reference, currency, debit_value, timestamp),
        )
        conn.execute(
            """INSERT INTO marketplace_ledger_entries
               (id, transaction_id, account_type, account_id, direction, currency, amount, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("le-" + uuid.uuid4().hex[:24], transaction_id, debit_type, debit_id, "debit", currency, debit_value, timestamp),
        )
        conn.execute(
            """INSERT INTO marketplace_ledger_entries
               (id, transaction_id, account_type, account_id, direction, currency, amount, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("le-" + uuid.uuid4().hex[:24], transaction_id, credit_type, credit_id, "credit", currency, credit_value, timestamp),
        )
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT hassle_ledger_post")
        conn.execute("RELEASE SAVEPOINT hassle_ledger_post")
        raise
    conn.execute("RELEASE SAVEPOINT hassle_ledger_post")
    return {"id": transaction_id, "idempotent_replay": False, "entries": 2}


ESCROW_EVENT_TYPES = {
    "deal_payment_requested", "payment_intent_created", "payment_pending",
    "payment_confirmed", "escrow_funded", "asset_transfer_requested",
    "asset_transfer_submitted", "asset_transfer_confirmed", "deal_completed",
    "deal_disputed", "refund_requested", "refund_confirmed", "payout_released",
    "deal_cancelled", "deal_expired", "dispute_resolution_approved",
    "dispute_resolution_ready",
}


def append_escrow_event(
    conn: Any,
    *,
    deal_id: str,
    actor_user_id: str,
    event_type: str,
    event_key: str,
    event_data: dict[str, Any] | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    deal = str(deal_id or "").strip()
    actor = str(actor_user_id or "").strip()
    kind = str(event_type or "").strip()
    key = str(event_key or "").strip()
    if not deal or not actor or not key or len(key) > 128:
        raise ValueError("Deal, actor and 1-128 character event key are required")
    if kind not in ESCROW_EVENT_TYPES:
        raise ValueError("Unsupported escrow event type")
    payload = json.dumps(event_data or {}, sort_keys=True, separators=(",", ":"))
    existing = conn.execute(
        "SELECT * FROM marketplace_escrow_events WHERE event_key = ?",
        (key,),
    ).fetchone()
    if existing is not None:
        actual = (
            existing["deal_id"], existing["actor_user_id"],
            existing["event_type"], existing["event_data"],
        )
        expected = (deal, actor, kind, payload)
        if actual != expected:
            raise PaymentConflict("Escrow event key was reused with different data")
        return {"id": existing["id"], "idempotent_replay": True}
    if conn.execute("SELECT 1 FROM marketplace_deals WHERE id = ?", (deal,)).fetchone() is None:
        raise PaymentStateError("Deal not found")
    timestamp = int(now if now is not None else __import__("time").time())
    event_id = "ee-" + uuid.uuid4().hex[:24]
    conn.execute(
        """INSERT INTO marketplace_escrow_events
           (id, deal_id, actor_user_id, event_type, event_key, event_data, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (event_id, deal, actor, kind, key, payload, timestamp),
    )
    return {"id": event_id, "idempotent_replay": False}


def record_dispute_resolution_approval(
    conn: Any,
    *,
    deal_id: str,
    admin_user_id: str,
    decision: str,
    idempotency_key: str,
    now: int | None = None,
) -> dict[str, Any]:
    deal = str(deal_id or "").strip()
    admin = str(admin_user_id or "").strip()
    choice = str(decision or "").strip()
    key = str(idempotency_key or "").strip()
    if not deal or not admin or not key or len(key) > 128:
        raise ValueError("Deal, administrator and 1-128 character idempotency key are required")
    if choice not in {"refund_buyer", "release_to_seller"}:
        raise ValueError("Unsupported dispute resolution decision")
    deal_row = conn.execute(
        "SELECT status FROM marketplace_deals WHERE id = ?",
        (deal,),
    ).fetchone()
    if deal_row is None:
        raise PaymentStateError("Deal not found")
    if deal_row["status"] != "disputed":
        raise PaymentStateError("Dispute resolution approvals require a disputed deal")
    prior_key = conn.execute(
        "SELECT * FROM marketplace_dispute_resolution_approvals WHERE idempotency_key = ?",
        (key,),
    ).fetchone()
    if prior_key is not None:
        if (
            prior_key["deal_id"] != deal
            or prior_key["admin_user_id"] != admin
            or prior_key["decision"] != choice
        ):
            raise PaymentConflict("Dispute approval idempotency key was reused with different data")
        count = conn.execute(
            "SELECT COUNT(*) FROM marketplace_dispute_resolution_approvals WHERE deal_id = ? AND decision = ?",
            (deal, choice),
        ).fetchone()[0]
        return {"approval_id": prior_key["id"], "approvals": int(count), "required": 2, "ready_for_execution": int(count) >= 2, "execution_enabled": False, "idempotent_replay": True}
    existing_admin = conn.execute(
        "SELECT * FROM marketplace_dispute_resolution_approvals WHERE deal_id = ? AND admin_user_id = ?",
        (deal, admin),
    ).fetchone()
    if existing_admin is not None:
        if existing_admin["decision"] == choice:
            count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_dispute_resolution_approvals WHERE deal_id = ? AND decision = ?",
                (deal, choice),
            ).fetchone()[0]
            return {"approval_id": existing_admin["id"], "approvals": int(count), "required": 2, "ready_for_execution": int(count) >= 2, "execution_enabled": False, "idempotent_replay": True}
        raise PaymentConflict("An administrator cannot approve conflicting dispute outcomes")
    existing_decision = conn.execute(
        "SELECT decision FROM marketplace_dispute_resolution_approvals WHERE deal_id = ? LIMIT 1",
        (deal,),
    ).fetchone()
    if existing_decision is not None and existing_decision["decision"] != choice:
        raise PaymentConflict("Dispute resolution approvals must agree on one outcome")
    existing_count = int(conn.execute(
        "SELECT COUNT(*) FROM marketplace_dispute_resolution_approvals WHERE deal_id = ?",
        (deal,),
    ).fetchone()[0])
    if existing_count >= 2:
        raise PaymentConflict("The two-administrator approval threshold has already been reached")
    timestamp = int(now if now is not None else __import__("time").time())
    approval_id = "dra-" + uuid.uuid4().hex[:24]
    conn.execute(
        """INSERT INTO marketplace_dispute_resolution_approvals
           (id, deal_id, admin_user_id, decision, idempotency_key, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (approval_id, deal, admin, choice, key, timestamp),
    )
    count = int(conn.execute(
        "SELECT COUNT(*) FROM marketplace_dispute_resolution_approvals WHERE deal_id = ? AND decision = ?",
        (deal, choice),
    ).fetchone()[0])
    append_escrow_event(
        conn, deal_id=deal, actor_user_id=admin,
        event_type="dispute_resolution_approved", event_key=key,
        event_data={"decision": choice, "approval_count": count, "approvals_required": 2},
        now=timestamp,
    )
    ready = count >= 2
    if ready:
        append_escrow_event(
            conn, deal_id=deal, actor_user_id=admin,
            event_type="dispute_resolution_ready",
            event_key=f"{deal}:dispute-resolution-ready:{choice}",
            event_data={"decision": choice, "approval_count": count, "execution_enabled": False},
            now=timestamp,
        )
    return {"approval_id": approval_id, "approvals": count, "required": 2, "ready_for_execution": ready, "execution_enabled": False, "idempotent_replay": False}


def list_escrow_events(conn: Any, deal_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, deal_id, actor_user_id, event_type, event_key, event_data, created_at
           FROM marketplace_escrow_events
           WHERE deal_id = ? ORDER BY created_at ASC, id ASC""",
        (str(deal_id or "").strip(),),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["event_data"] = json.loads(item["event_data"] or "{}")
        except (TypeError, ValueError):
            item["event_data"] = {}
        items.append(item)
    return items


DEAL_STATUS_TRANSITIONS = {
    "payment_not_enabled": {"awaiting_payment", "cancelled", "expired"},
    "awaiting_payment": {"payment_pending", "cancelled", "expired"},
    "payment_pending": {"paid", "awaiting_payment", "expired"},
    "paid": {"delivery_pending", "disputed"},
    "delivery_pending": {"completed", "disputed"},
    "disputed": {"delivery_pending", "completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
    "expired": set(),
}


def transition_deal_status(
    conn: Any,
    *,
    deal_id: str,
    new_status: str,
    actor_user_id: str,
    idempotency_key: str,
    event_type: str,
    event_data: dict[str, Any] | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    deal = str(deal_id or "").strip()
    target = str(new_status or "").strip()
    actor = str(actor_user_id or "").strip()
    key = str(idempotency_key or "").strip()
    if not deal or not actor or not key:
        raise ValueError("Deal, actor and idempotency key are required")
    row = conn.execute(
        "SELECT status FROM marketplace_deals WHERE id = ?",
        (deal,),
    ).fetchone()
    if row is None:
        raise PaymentStateError("Deal not found")
    current = str(row["status"])
    if target == current:
        replay = conn.execute(
            "SELECT * FROM marketplace_escrow_events WHERE event_key = ?",
            (key,),
        ).fetchone()
        if replay is not None:
            try:
                recorded_data = json.loads(replay["event_data"] or "{}")
            except (TypeError, ValueError):
                recorded_data = {}
            expected_data = dict(event_data or {})
            recorded_extra = {name: value for name, value in recorded_data.items() if name not in {"from", "to"}}
            if (
                replay["deal_id"] == deal
                and replay["actor_user_id"] == actor
                and replay["event_type"] == event_type
                and recorded_data.get("to") == target
                and recorded_extra == expected_data
            ):
                return {"deal_id": deal, "status": current, "idempotent_replay": True, "event_id": replay["id"]}
            raise PaymentConflict("Deal transition idempotency key was reused with different data")
        raise PaymentConflict("Deal is already in the requested state under a different operation")
    if target not in DEAL_STATUS_TRANSITIONS.get(current, set()):
        raise PaymentStateError(f"Invalid deal transition: {current} -> {target}")
    timestamp = int(now if now is not None else __import__("time").time())
    event = append_escrow_event(
        conn,
        deal_id=deal,
        actor_user_id=actor,
        event_type=event_type,
        event_key=key,
        event_data={**(event_data or {}), "from": current, "to": target},
        now=timestamp,
    )
    changed = conn.execute(
        "UPDATE marketplace_deals SET status = ? WHERE id = ? AND status = ?",
        (target, deal, current),
    )
    if changed.rowcount != 1:
        raise PaymentConflict("Deal state changed concurrently")
    return {
        "deal_id": deal,
        "status": target,
        "idempotent_replay": event["idempotent_replay"],
        "event_id": event["id"],
    }


def verify_ledger_transaction(conn: Any, transaction_id: str) -> dict[str, Any]:
    transaction = conn.execute(
        "SELECT * FROM marketplace_ledger_transactions WHERE id = ?",
        (transaction_id,),
    ).fetchone()
    if transaction is None:
        raise PaymentStateError("Ledger transaction not found")
    rows = conn.execute(
        """SELECT direction, currency, amount FROM marketplace_ledger_entries
           WHERE transaction_id = ?""",
        (transaction_id,),
    ).fetchall()
    debits = [Decimal(row["amount"]) for row in rows if row["direction"] == "debit"]
    credits = [Decimal(row["amount"]) for row in rows if row["direction"] == "credit"]
    balanced = (
        len(rows) == 2
        and len(debits) == 1
        and len(credits) == 1
        and debits[0] == credits[0]
        and all(row["currency"] == transaction["currency"] for row in rows)
        and debits[0] == Decimal(transaction["amount"])
    )
    return {"transaction_id": transaction_id, "balanced": balanced, "entry_count": len(rows)}
