"""Safe GRAM withdrawal reservation queue.

This module reserves internal ledger funds and records a payout request. It does
not sign or submit a blockchain transfer. A withdrawal is only completed after
an external multisig signer has submitted a transaction and the chain observer
has verified the actual outgoing transfer.
"""
from __future__ import annotations

import time
import uuid
from decimal import Decimal
from typing import Any

from database import begin_write
from gram_deposits import gram_to_nano
from marketplace_payments import PaymentConflict, PaymentStateError, post_ledger_transaction

NANO_PER_GRAM = 1_000_000_000
WITHDRAWAL_STATES = {"queued", "awaiting_multisig", "submitted", "confirmed", "failed", "cancelled"}


class GramWithdrawalError(ValueError):
    """A GRAM withdrawal request failed validation."""


def _validate_destination(value: str) -> str:
    destination = str(value or "").strip()
    if not destination or len(destination) > 128:
        raise GramWithdrawalError("A valid GRAM destination address is required")
    try:
        from tonsdk.utils import Address
        address = Address(destination)
        if int(address.wc) != 0:
            raise ValueError("Unsupported workchain")
        return destination
    except Exception as exc:
        raise GramWithdrawalError("Invalid GRAM destination address") from exc


def _amount_decimal(amount_nano: int) -> Decimal:
    return Decimal(int(amount_nano)) / NANO_PER_GRAM


def initialize_gram_withdrawal_schema(conn: Any) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gram_withdrawals (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            network TEXT NOT NULL CHECK(network IN ('mainnet','testnet')),
            destination_address TEXT NOT NULL,
            amount_nano BIGINT NOT NULL CHECK(amount_nano > 0),
            fee_nano BIGINT NOT NULL CHECK(fee_nano >= 0),
            payout_nano BIGINT NOT NULL CHECK(payout_nano > 0),
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK(status IN ('queued','awaiting_multisig','submitted','confirmed','failed','cancelled')),
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL,
            submitted_at BIGINT,
            confirmed_at BIGINT,
            chain_tx_hash TEXT,
            chain_lt TEXT,
            failure_code TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_gram_withdrawals_user
        ON gram_withdrawals(user_id, created_at DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_gram_withdrawals_queue
        ON gram_withdrawals(status, created_at ASC)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gram_withdrawal_events (
            id TEXT PRIMARY KEY,
            withdrawal_id TEXT NOT NULL,
            event_key TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            event_data TEXT NOT NULL DEFAULT '{}',
            created_at BIGINT NOT NULL,
            FOREIGN KEY(withdrawal_id) REFERENCES gram_withdrawals(id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_gram_withdrawal_events_withdrawal
        ON gram_withdrawal_events(withdrawal_id, created_at ASC)
    """)


def _available_balance_nano(conn: Any, user_id: str) -> int:
    rows = conn.execute(
        """SELECT direction,amount FROM marketplace_ledger_entries
           WHERE account_type='user_balance' AND account_id=? AND currency='TON'""",
        (str(user_id),),
    ).fetchall()
    balance = Decimal("0")
    for row in rows:
        amount = Decimal(str(row["amount"]))
        balance += amount if row["direction"] == "credit" else -amount
    nano = balance * NANO_PER_GRAM
    if nano != nano.to_integral_value() or nano < 0:
        raise GramWithdrawalError("The available GRAM balance requires reconciliation")
    return int(nano)


def _append_event(conn: Any, withdrawal_id: str, event_key: str, event_type: str, data: dict[str, Any], now: int) -> None:
    import json

    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    existing = conn.execute(
        "SELECT withdrawal_id,event_type,event_data FROM gram_withdrawal_events WHERE event_key=?",
        (event_key,),
    ).fetchone()
    if existing is not None:
        if (existing["withdrawal_id"], existing["event_type"], existing["event_data"]) != (withdrawal_id, event_type, payload):
            raise PaymentConflict("Withdrawal event key was reused with different data")
        return
    conn.execute(
        """INSERT INTO gram_withdrawal_events
           (id,withdrawal_id,event_key,event_type,event_data,created_at)
           VALUES (?,?,?,?,?,?)""",
        ("gwe_" + uuid.uuid4().hex, withdrawal_id, event_key, event_type, payload, now),
    )


def create_gram_withdrawal(
    conn: Any, *, user_id: str, destination_address: str, amount_gram: Any,
    network: str, idempotency_key: str, fee_bps: int = 0,
    min_amount_nano: int = 1, max_amount_nano: int = 100_000_000_000_000,
    now: int | None = None,
) -> dict[str, Any]:
    user = str(user_id or "").strip()
    key = str(idempotency_key or "").strip()
    if not user.isdigit():
        raise GramWithdrawalError("A valid Telegram user is required")
    if not key or len(key) > 128:
        raise GramWithdrawalError("A 1-128 character idempotency key is required")
    key = f"{user}:{key}"
    if network not in {"mainnet", "testnet"}:
        raise GramWithdrawalError("Unsupported GRAM network")
    destination = _validate_destination(destination_address)
    amount_nano = gram_to_nano(amount_gram)
    if not int(min_amount_nano) <= amount_nano <= int(max_amount_nano):
        raise GramWithdrawalError("GRAM withdrawal amount is outside configured limits")
    if isinstance(fee_bps, bool) or not isinstance(fee_bps, int) or not 0 <= fee_bps <= 200:
        raise GramWithdrawalError("Withdrawal fee must be between 0 and 2 percent")
    fee_nano = (amount_nano * fee_bps + 9999) // 10000
    payout_nano = amount_nano - fee_nano
    if payout_nano <= 0:
        raise GramWithdrawalError("Withdrawal amount is too small after fees")
    timestamp = int(time.time()) if now is None else int(now)

    begin_write(conn, f"gram-withdrawal:{user}")
    try:
        existing = conn.execute(
            "SELECT * FROM gram_withdrawals WHERE idempotency_key=?", (key,)
        ).fetchone()
        if existing is not None:
            row = dict(existing)
            expected = (destination, network, amount_nano, fee_nano, payout_nano)
            actual = (row["destination_address"], row["network"], int(row["amount_nano"]),
                      int(row["fee_nano"]), int(row["payout_nano"]))
            if expected != actual:
                raise PaymentConflict("GRAM withdrawal idempotency key was reused with different parameters")
            conn.rollback()
            return row

        available_nano = _available_balance_nano(conn, user)
        if available_nano < amount_nano:
            raise GramWithdrawalError("Insufficient available GRAM balance")
        withdrawal_id = "gw_" + uuid.uuid4().hex
        conn.execute(
            """INSERT INTO gram_withdrawals
               (id,user_id,network,destination_address,amount_nano,fee_nano,payout_nano,
                status,idempotency_key,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,'queued',?,?,?)""",
            (withdrawal_id, user, network, destination, amount_nano, fee_nano,
             payout_nano, key, timestamp, timestamp),
        )
        post_ledger_transaction(
            conn, idempotency_key=f"gram-withdrawal:{withdrawal_id}:reserve",
            reference_type="withdrawal", reference_id=withdrawal_id, currency="TON",
            debit_account=("user_balance", user),
            credit_account=("user_withdrawal_hold", withdrawal_id),
            debit_amount=_amount_decimal(amount_nano), credit_amount=_amount_decimal(amount_nano),
            now=timestamp,
        )
        if fee_nano:
            post_ledger_transaction(
                conn, idempotency_key=f"gram-withdrawal:{withdrawal_id}:fee",
                reference_type="withdrawal", reference_id=withdrawal_id, currency="TON",
                debit_account=("user_withdrawal_hold", withdrawal_id),
                credit_account=("platform_fee_pending", withdrawal_id),
                debit_amount=_amount_decimal(fee_nano), credit_amount=_amount_decimal(fee_nano),
                now=timestamp,
            )
        _append_event(
            conn, withdrawal_id, f"{withdrawal_id}:created", "created",
            {"amount_nano": str(amount_nano), "fee_nano": str(fee_nano),
             "payout_nano": str(payout_nano), "network": network}, timestamp,
        )
        row = dict(conn.execute(
            "SELECT * FROM gram_withdrawals WHERE id=?", (withdrawal_id,)
        ).fetchone())
        conn.commit()
        return row
    except BaseException:
        conn.rollback()
        raise


def get_gram_withdrawal(conn: Any, withdrawal_id: str, user_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM gram_withdrawals WHERE id=? AND user_id=?",
        (str(withdrawal_id), str(user_id)),
    ).fetchone()
    if row is None:
        raise PaymentStateError("GRAM withdrawal not found")
    return dict(row)


def list_gram_withdrawals(conn: Any, user_id: str, limit: int = 30) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 100))
    rows = conn.execute(
        """SELECT id,network,destination_address,amount_nano,fee_nano,payout_nano,
                  status,created_at,updated_at,submitted_at,confirmed_at,chain_tx_hash
           FROM gram_withdrawals WHERE user_id=?
           ORDER BY created_at DESC LIMIT ?""",
        (str(user_id), bounded),
    ).fetchall()
    return [dict(row) for row in rows]


def transition_gram_withdrawal(
    conn: Any, *, withdrawal_id: str, new_status: str, event_key: str,
    chain_tx_hash: str | None = None, chain_lt: str | None = None,
    failure_code: str | None = None, chain_confirmation_verified: bool = False,
    chain_failure_verified: bool = False, now: int | None = None,
) -> dict[str, Any]:
    transitions = {
        "queued": {"awaiting_multisig", "failed", "cancelled"},
        "awaiting_multisig": {"submitted", "failed"},
        "submitted": {"confirmed", "failed"},
    }
    if new_status not in WITHDRAWAL_STATES:
        raise GramWithdrawalError("Unsupported withdrawal status")
    timestamp = int(time.time()) if now is None else int(now)
    begin_write(conn, f"gram-withdrawal-state:{withdrawal_id}")
    try:
        row = conn.execute(
            "SELECT * FROM gram_withdrawals WHERE id=?", (withdrawal_id,)
        ).fetchone()
        if row is None:
            raise PaymentStateError("GRAM withdrawal not found")
        current = str(row["status"])
        if new_status == current:
            conn.rollback()
            return dict(row)
        if new_status not in transitions.get(current, set()):
            raise PaymentStateError(f"Invalid withdrawal transition: {current} -> {new_status}")
        if new_status == "submitted" and (not chain_tx_hash or not chain_lt):
            raise GramWithdrawalError("Submitted withdrawal requires an on-chain transaction reference")
        if new_status == "confirmed":
            if not chain_confirmation_verified:
                raise GramWithdrawalError("Confirmed withdrawal requires server-verified on-chain execution")
            if not row["chain_tx_hash"] or not row["chain_lt"]:
                raise GramWithdrawalError("Confirmed withdrawal requires a submitted transaction")
        if new_status == "failed" and current == "submitted" and not chain_failure_verified:
            raise GramWithdrawalError("Submitted withdrawal can only be released after server-verified chain failure")
            # This transition is intentionally callable only after an external
            # observer has verified the actual outgoing transfer from treasury.
            amount = _amount_decimal(int(row["payout_nano"]))
            post_ledger_transaction(
                conn, idempotency_key=f"gram-withdrawal:{withdrawal_id}:paid",
                reference_type="withdrawal", reference_id=withdrawal_id, currency="TON",
                debit_account=("user_withdrawal_hold", withdrawal_id),
                credit_account=("external_chain", f"{row['network']}:{row['destination_address']}"),
                debit_amount=amount, credit_amount=amount, now=timestamp,
            )
        if new_status in {"failed", "cancelled"}:
            net = int(row["payout_nano"])
            fee = int(row["fee_nano"])
            if net:
                post_ledger_transaction(
                    conn, idempotency_key=f"gram-withdrawal:{withdrawal_id}:release-net",
                    reference_type="withdrawal", reference_id=withdrawal_id, currency="TON",
                    debit_account=("user_withdrawal_hold", withdrawal_id),
                    credit_account=("user_balance", row["user_id"]),
                    debit_amount=_amount_decimal(net), credit_amount=_amount_decimal(net), now=timestamp,
                )
            if fee:
                post_ledger_transaction(
                    conn, idempotency_key=f"gram-withdrawal:{withdrawal_id}:release-fee",
                    reference_type="withdrawal", reference_id=withdrawal_id, currency="TON",
                    debit_account=("platform_fee_pending", withdrawal_id),
                    credit_account=("user_balance", row["user_id"]),
                    debit_amount=_amount_decimal(fee), credit_amount=_amount_decimal(fee), now=timestamp,
                )
        updated = conn.execute(
            """UPDATE gram_withdrawals SET status=?,updated_at=?,
               submitted_at=CASE WHEN ?='submitted' THEN ? ELSE submitted_at END,
               confirmed_at=CASE WHEN ?='confirmed' THEN ? ELSE confirmed_at END,
               chain_tx_hash=COALESCE(?,chain_tx_hash),chain_lt=COALESCE(?,chain_lt),
               failure_code=? WHERE id=? AND status=?""",
            (new_status, timestamp, new_status, timestamp, new_status, timestamp,
             chain_tx_hash, chain_lt, failure_code, withdrawal_id, current),
        )
        if updated.rowcount != 1:
            raise PaymentConflict("GRAM withdrawal changed concurrently")
        _append_event(
            conn, withdrawal_id, event_key, new_status,
            {"status": new_status, "tx_hash": chain_tx_hash or row["chain_tx_hash"],
             "failure_code": failure_code}, timestamp,
        )
        result = dict(conn.execute(
            "SELECT * FROM gram_withdrawals WHERE id=?", (withdrawal_id,)
        ).fetchone())
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise
