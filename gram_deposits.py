"""GRAM deposit intents and TONAPI transaction verification.

The client only receives a deposit instruction. A deposit is credited exclusively
after the server independently observes a successful incoming transaction in the
treasury account history and matches destination, exact nano amount, memo and
intent lifetime. Client callbacks and submitted transaction hashes are ignored.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from database import begin_write
from marketplace_payments import PaymentConflict, PaymentStateError, post_ledger_transaction

NANO_PER_GRAM = 1_000_000_000
DEPOSIT_STATES = {"pending", "confirmed", "expired", "review"}


class GramDepositError(ValueError):
    """A deposit request or provider transaction failed validation."""


def gram_to_nano(value: Any) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GramDepositError("Invalid GRAM amount") from exc
    if not amount.is_finite() or amount <= 0:
        raise GramDepositError("GRAM amount must be positive")
    nano = amount * NANO_PER_GRAM
    if nano != nano.to_integral_value():
        raise GramDepositError("GRAM amount supports at most 9 decimal places")
    if nano > Decimal("9223372036854775807"):
        raise GramDepositError("GRAM amount is too large")
    return int(nano)


def initialize_gram_deposit_schema(conn: Any) -> None:
    """Portable SQLite/PostgreSQL schema; all values use integer nano-GRAM."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gram_deposit_intents (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            network TEXT NOT NULL CHECK(network IN ('mainnet','testnet')),
            treasury_address TEXT NOT NULL,
            amount_nano BIGINT NOT NULL CHECK(amount_nano > 0),
            memo TEXT NOT NULL UNIQUE,
            idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','confirmed','expired','review')),
            created_at BIGINT NOT NULL,
            expires_at BIGINT NOT NULL,
            confirmed_at BIGINT,
            chain_tx_hash TEXT,
            chain_lt TEXT,
            ledger_transaction_id TEXT,
            CHECK(expires_at > created_at)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_gram_deposit_intents_user
        ON gram_deposit_intents(user_id, created_at DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_gram_deposit_intents_pending
        ON gram_deposit_intents(status, expires_at)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gram_deposit_chain_events (
            id TEXT PRIMARY KEY,
            network TEXT NOT NULL,
            tx_hash TEXT NOT NULL,
            tx_lt TEXT NOT NULL,
            deposit_id TEXT NOT NULL,
            sender_address TEXT NOT NULL DEFAULT '',
            recipient_address TEXT NOT NULL,
            amount_nano BIGINT NOT NULL,
            memo_hash TEXT NOT NULL,
            observed_at BIGINT NOT NULL,
            UNIQUE(network, tx_hash, tx_lt),
            UNIQUE(deposit_id),
            FOREIGN KEY(deposit_id) REFERENCES gram_deposit_intents(id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_gram_deposit_chain_events_deposit
        ON gram_deposit_chain_events(deposit_id)
    """)


def create_gram_deposit_intent(
    conn: Any, *, user_id: str, treasury_address: str, amount_gram: Any,
    network: str, idempotency_key: str, now: int | None = None,
    ttl_seconds: int = 1800,
) -> dict[str, Any]:
    user = str(user_id or "").strip()
    address = str(treasury_address or "").strip()
    key = str(idempotency_key or "").strip()
    if not user or not user.isdigit():
        raise GramDepositError("A valid Telegram user is required")
    if not address or len(address) > 128:
        raise GramDepositError("GRAM treasury address is not configured")
    if network not in {"mainnet", "testnet"}:
        raise GramDepositError("Unsupported GRAM network")
    if not key or len(key) > 128:
        raise GramDepositError("A 1-128 character idempotency key is required")
    if not 60 <= int(ttl_seconds) <= 86400:
        raise GramDepositError("Deposit lifetime must be between 1 minute and 24 hours")
    amount_nano = gram_to_nano(amount_gram)
    timestamp = int(time.time()) if now is None else int(now)
    existing = conn.execute(
        "SELECT * FROM gram_deposit_intents WHERE idempotency_key = ?", (key,)
    ).fetchone()
    if existing is not None:
        row = dict(existing)
        expected = (user, network, address, amount_nano)
        actual = (row["user_id"], row["network"], row["treasury_address"], int(row["amount_nano"]))
        if expected != actual:
            raise PaymentConflict("GRAM deposit idempotency key was reused with different parameters")
        return row
    deposit_id = "gd_" + uuid.uuid4().hex
    memo = "HASSLE:" + secrets.token_urlsafe(24)
    conn.execute(
        """INSERT INTO gram_deposit_intents
           (id,user_id,network,treasury_address,amount_nano,memo,idempotency_key,
            status,created_at,expires_at)
           VALUES (?,?,?,?,?,?,?,'pending',?,?)""",
        (deposit_id, user, network, address, amount_nano, memo, key,
         timestamp, timestamp + int(ttl_seconds)),
    )
    return dict(conn.execute(
        "SELECT * FROM gram_deposit_intents WHERE id = ?", (deposit_id,)
    ).fetchone())


def get_gram_deposit_intent(conn: Any, deposit_id: str, user_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM gram_deposit_intents WHERE id = ? AND user_id = ?",
        (str(deposit_id), str(user_id)),
    ).fetchone()
    if row is None:
        raise PaymentStateError("GRAM deposit not found")
    return dict(row)


def extract_incoming_transaction(transaction: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize the TONAPI account-transaction shape without trusting client data."""
    if not isinstance(transaction, dict):
        return None
    incoming = transaction.get("in_msg") or transaction.get("in_message")
    if not isinstance(incoming, dict):
        return None
    success = transaction.get("success")
    aborted = transaction.get("aborted", False)
    if success is not True or aborted is True or incoming.get("bounced") is True:
        return None
    recipient = str(incoming.get("destination") or incoming.get("dest") or "").strip()
    value = incoming.get("value")
    try:
        amount_nano = int(str(value))
    except (TypeError, ValueError):
        return None
    if not recipient or amount_nano <= 0:
        return None
    decoded = incoming.get("decoded_body")
    if not isinstance(decoded, dict):
        decoded = {}
    msg_data = incoming.get("msg_data")
    if not isinstance(msg_data, dict):
        msg_data = {}
    memo = (
        incoming.get("comment") or incoming.get("message")
        or decoded.get("text") or decoded.get("comment")
        or msg_data.get("text") or ""
    )
    tx_hash = str(transaction.get("hash") or transaction.get("transaction_hash") or "").strip()
    tx_lt = str(transaction.get("lt") or transaction.get("logical_time") or "").strip()
    if not tx_hash or not tx_lt:
        return None
    return {
        "tx_hash": tx_hash,
        "tx_lt": tx_lt,
        "utime": int(transaction.get("utime") or transaction.get("timestamp") or 0),
        "sender_address": str(incoming.get("source") or ""),
        "recipient_address": recipient,
        "amount_nano": amount_nano,
        "memo": str(memo).strip(),
    }


def _canonical_address(value: str) -> str:
    """Normalize friendly/raw TON address forms when the SDK helper is available."""
    try:
        from ton_nft_inventory import normalise_ton_address
        return str(normalise_ton_address(value)).strip()
    except Exception:
        return str(value or "").strip()


def confirm_gram_deposit(
    conn: Any, *, network: str, transaction: dict[str, Any],
    now: int | None = None,
) -> dict[str, Any] | None:
    """Match and credit one verified chain transaction in the same DB transaction."""
    event = extract_incoming_transaction(transaction)
    if event is None:
        return None
    timestamp = int(time.time()) if now is None else int(now)
    with conn:
        begin_write(conn, f"gram-deposit:{network}:{event['tx_hash']}:{event['tx_lt']}")
        intent = conn.execute(
            """SELECT * FROM gram_deposit_intents
               WHERE network = ? AND memo = ? AND status = 'pending'""",
            (network, event["memo"]),
        ).fetchone()
        if intent is None:
            return None
        row = dict(intent)
        if (
            _canonical_address(event["recipient_address"]) != _canonical_address(row["treasury_address"])
            or int(event["amount_nano"]) != int(row["amount_nano"])
            or int(event["utime"]) < int(row["created_at"])
            or int(event["utime"]) > int(row["expires_at"])
        ):
            return None

        event_id = "gce_" + uuid.uuid4().hex
        memo_hash = hashlib.sha256(event["memo"].encode("utf-8")).hexdigest()
        conn.execute(
            """INSERT INTO gram_deposit_chain_events
               (id,network,tx_hash,tx_lt,deposit_id,sender_address,recipient_address,
                amount_nano,memo_hash,observed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (event_id, network, event["tx_hash"], event["tx_lt"], row["id"],
             event["sender_address"], event["recipient_address"], event["amount_nano"],
             memo_hash, timestamp),
        )
        ledger = post_ledger_transaction(
            conn,
            idempotency_key=f"gram-deposit:{network}:{event['tx_hash']}:{event['tx_lt']}",
            reference_type="payment_intent",
            reference_id=row["id"],
            currency="TON",
            debit_account=("external_chain", f"{network}:{row['treasury_address']}"),
            credit_account=("user_balance", row["user_id"]),
            debit_amount=Decimal(int(event["amount_nano"])) / NANO_PER_GRAM,
            credit_amount=Decimal(int(event["amount_nano"])) / NANO_PER_GRAM,
            now=timestamp,
        )
        conn.execute(
            """UPDATE gram_deposit_intents
               SET status='confirmed', confirmed_at=?, chain_tx_hash=?,
                   chain_lt=?, ledger_transaction_id=?
               WHERE id=? AND status='pending'""",
            (timestamp, event["tx_hash"], event["tx_lt"], ledger["id"], row["id"]),
        )
        return {"deposit_id": row["id"], "user_id": row["user_id"],
                "amount_nano": int(event["amount_nano"]), "ledger_transaction_id": ledger["id"]}


def list_pending_gram_deposits(conn: Any, *, network: str, limit: int = 500) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 1000))
    rows = conn.execute(
        """SELECT id,memo,amount_nano,treasury_address,created_at,expires_at
           FROM gram_deposit_intents WHERE network=? AND status='pending'
           ORDER BY created_at ASC LIMIT ?""",
        (network, bounded),
    ).fetchall()
    return [dict(row) for row in rows]


def expire_gram_deposit_intents(conn: Any, *, now: int | None = None) -> int:
    timestamp = int(time.time()) if now is None else int(now)
    with conn:
        begin_write(conn, "gram-deposit-expiry-sweep")
        result = conn.execute(
            """UPDATE gram_deposit_intents SET status='expired'
               WHERE status='pending' AND expires_at < ?""",
            (timestamp,),
        )
        return int(result.rowcount)
