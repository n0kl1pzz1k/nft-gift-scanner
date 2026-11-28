"""GRAM deposit intents and verified TON-chain crediting.

The client only receives a TON Connect transaction proposal. A deposit is
credited exclusively by the server-side TONAPI indexer after matching the
recipient, exact nano amount and unique comment payload on a completed event.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from database import begin_write, select_for_update
from marketplace import _db
from ton_nft_inventory import normalise_ton_address

_LOG = logging.getLogger("hassle_nft.gram")
NANO_PER_GRAM = 1_000_000_000


class GramWithdrawalRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=9)
    destination_address: str = Field(min_length=10, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=128)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -9:
            raise ValueError("Amount must be positive with at most 9 decimal places")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if not value or any(not (ch.isalnum() or ch in "._:-") for ch in value):
            raise ValueError("Invalid idempotency key")
        return value


class GramDepositRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=9)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("Amount must be positive")
        if value.as_tuple().exponent < -9:
            raise ValueError("GRAM amount supports at most 9 decimal places")
        return value


def _amount_to_nano(amount: Decimal) -> int:
    scaled = amount * NANO_PER_GRAM
    if scaled != scaled.to_integral_value():
        raise ValueError("GRAM amount supports at most 9 decimal places")
    value = int(scaled)
    if value <= 0 or value > 2**63 - 1:
        raise ValueError("GRAM amount is outside the supported range")
    return value


def _nano_to_gram_text(amount_nano: int) -> str:
    value = Decimal(int(amount_nano)) / Decimal(NANO_PER_GRAM)
    return format(value.normalize(), "f")


def _network() -> str:
    network = os.getenv("HASSLE_TON_NETWORK", "mainnet").strip().lower()
    if network not in {"mainnet", "testnet"}:
        raise RuntimeError("HASSLE_TON_NETWORK must be mainnet or testnet")
    return network


def _treasury() -> str:
    address = os.getenv("HASSLE_TON_TREASURY_ADDRESS", "").strip()
    if not address:
        raise RuntimeError("GRAM treasury address is not configured")
    return normalise_ton_address(address)


def _require_deposits_enabled() -> tuple[str, str, str]:
    if os.getenv("HASSLE_GRAM_DEPOSITS_ENABLED", "0").strip() != "1":
        raise HTTPException(status_code=503, detail="GRAM deposits are temporarily unavailable")
    token = os.getenv("TONAPI_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="GRAM deposit indexer is not configured")
    try:
        network = _network()
        treasury = _treasury()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="GRAM treasury configuration is invalid") from exc
    return network, treasury, token


async def _verify_active_gram_treasury(network: str, treasury: str, token: str) -> None:
    """Fail closed if the configured deposit destination is not an active account."""
    base = "https://tonapi.io" if network == "mainnet" else "https://testnet.tonapi.io"
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
            response = await client.get(
                f"{base}/v2/accounts/{treasury}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            response.raise_for_status()
            account = response.json()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="GRAM treasury status could not be verified") from exc
    if not isinstance(account, dict) or account.get("status") != "active" or account.get("is_scam") is not False:
        raise HTTPException(
            status_code=503,
            detail="GRAM treasury is not confirmed as an active, non-scam account",
        )


def _comment_payload_boc(comment: str) -> str:
    """Build the standard TON text-comment cell for TON Connect."""
    try:
        from tonsdk.boc import Cell
        cell = Cell()
        cell.bits.write_uint(0, 32)
        cell.bits.write_string(comment)
        return base64.b64encode(cell.to_boc(False)).decode("ascii")
    except Exception as exc:
        raise RuntimeError("Could not encode TON Connect comment payload") from exc


def init_gram_deposit_schema() -> None:
    with _db() as conn:
        statements = (
            """CREATE TABLE IF NOT EXISTS gram_deposit_intents (
                id TEXT PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                network TEXT NOT NULL CHECK(network IN ('mainnet','testnet')),
                treasury_address TEXT NOT NULL,
                amount_nano TEXT NOT NULL,
                memo TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK(status IN ('pending','confirmed','expired')),
                created_at BIGINT NOT NULL,
                expires_at BIGINT NOT NULL,
                confirmed_at BIGINT,
                tx_hash TEXT,
                chain_event_id TEXT UNIQUE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_gram_deposit_pending
                ON gram_deposit_intents(status, expires_at)""",
            """CREATE INDEX IF NOT EXISTS idx_gram_deposit_user
                ON gram_deposit_intents(telegram_id, created_at DESC)""",
            """CREATE TABLE IF NOT EXISTS gram_withdrawals (
                id TEXT PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                network TEXT NOT NULL CHECK(network IN ('mainnet','testnet')),
                destination_address TEXT NOT NULL,
                gross_amount_nano TEXT NOT NULL,
                fee_nano TEXT NOT NULL,
                net_amount_nano TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued_for_multisig','cancelled','submitted','confirmed','failed')),
                idempotency_key TEXT NOT NULL,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                payout_tx_hash TEXT,
                UNIQUE(telegram_id,idempotency_key),
                UNIQUE(payout_tx_hash)
            )""",
            """CREATE INDEX IF NOT EXISTS idx_gram_withdrawals_user
                ON gram_withdrawals(telegram_id,created_at DESC)""",
            """CREATE INDEX IF NOT EXISTS idx_gram_withdrawals_queue
                ON gram_withdrawals(status,created_at)""",
            """CREATE TABLE IF NOT EXISTS gram_withdrawal_events (
                id TEXT PRIMARY KEY,
                withdrawal_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_key TEXT NOT NULL UNIQUE,
                event_data TEXT NOT NULL DEFAULT '{}',
                created_at BIGINT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS gram_chain_events (
                id TEXT PRIMARY KEY,
                network TEXT NOT NULL,
                chain_event_id TEXT NOT NULL,
                action_index INTEGER NOT NULL,
                tx_hash TEXT NOT NULL,
                deposit_id TEXT NOT NULL,
                amount_nano TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                created_at BIGINT NOT NULL,
                UNIQUE(network, chain_event_id, action_index),
                UNIQUE(deposit_id)
            )""",
        )
        for statement in statements:
            conn.execute(statement)
        # Keep chain and withdrawal evidence append-only on both supported DBs.
        if conn.__class__.__module__ == "sqlite3":
            conn.execute("""CREATE TRIGGER IF NOT EXISTS gram_chain_events_no_update
                BEFORE UPDATE ON gram_chain_events
                BEGIN SELECT RAISE(ABORT, 'GRAM chain events are immutable'); END""")
            conn.execute("""CREATE TRIGGER IF NOT EXISTS gram_chain_events_no_delete
                BEFORE DELETE ON gram_chain_events
                BEGIN SELECT RAISE(ABORT, 'GRAM chain events are immutable'); END""")
            conn.execute("""CREATE TRIGGER IF NOT EXISTS gram_withdrawal_events_no_update
                BEFORE UPDATE ON gram_withdrawal_events
                BEGIN SELECT RAISE(ABORT, 'GRAM withdrawal events are immutable'); END""")
            conn.execute("""CREATE TRIGGER IF NOT EXISTS gram_withdrawal_events_no_delete
                BEFORE DELETE ON gram_withdrawal_events
                BEGIN SELECT RAISE(ABORT, 'GRAM withdrawal events are immutable'); END""")
        elif bool(getattr(conn, "supports_for_update", False)):
            conn.execute("""CREATE OR REPLACE FUNCTION hassle_gram_chain_events_immutable()
                RETURNS trigger LANGUAGE plpgsql AS $hassle_gram$
                BEGIN
                    RAISE EXCEPTION USING MESSAGE = 'GRAM chain events are immutable';
                END;
                $hassle_gram$""")
            conn.execute("DROP TRIGGER IF EXISTS gram_chain_events_no_update ON gram_chain_events")
            conn.execute("DROP TRIGGER IF EXISTS gram_chain_events_no_delete ON gram_chain_events")
            conn.execute("DROP TRIGGER IF EXISTS gram_chain_events_no_truncate ON gram_chain_events")
            conn.execute("""CREATE TRIGGER gram_chain_events_no_update
                BEFORE UPDATE ON gram_chain_events FOR EACH ROW
                EXECUTE FUNCTION hassle_gram_chain_events_immutable()""")
            conn.execute("""CREATE TRIGGER gram_chain_events_no_delete
                BEFORE DELETE ON gram_chain_events FOR EACH ROW
                EXECUTE FUNCTION hassle_gram_chain_events_immutable()""")
            conn.execute("""CREATE TRIGGER gram_chain_events_no_truncate
                BEFORE TRUNCATE ON gram_chain_events FOR EACH STATEMENT
                EXECUTE FUNCTION hassle_gram_chain_events_immutable()""")
            conn.execute("DROP TRIGGER IF EXISTS gram_withdrawal_events_no_update ON gram_withdrawal_events")
            conn.execute("DROP TRIGGER IF EXISTS gram_withdrawal_events_no_delete ON gram_withdrawal_events")
            conn.execute("DROP TRIGGER IF EXISTS gram_withdrawal_events_no_truncate ON gram_withdrawal_events")
            conn.execute("""CREATE TRIGGER gram_withdrawal_events_no_update
                BEFORE UPDATE ON gram_withdrawal_events FOR EACH ROW
                EXECUTE FUNCTION hassle_gram_chain_events_immutable()""")
            conn.execute("""CREATE TRIGGER gram_withdrawal_events_no_delete
                BEFORE DELETE ON gram_withdrawal_events FOR EACH ROW
                EXECUTE FUNCTION hassle_gram_chain_events_immutable()""")
            conn.execute("""CREATE TRIGGER gram_withdrawal_events_no_truncate
                BEFORE TRUNCATE ON gram_withdrawal_events FOR EACH STATEMENT
                EXECUTE FUNCTION hassle_gram_chain_events_immutable()""")


def create_gram_deposit(telegram_id: str, amount: Decimal, now: int | None = None) -> dict[str, Any]:
    network, treasury, _ = _require_deposits_enabled()
    user_id = str(telegram_id).strip()
    if not user_id.isdigit():
        raise ValueError("Invalid Telegram user ID")
    amount_nano = _amount_to_nano(amount)
    timestamp = int(time.time()) if now is None else int(now)
    deposit_id = "gd-" + uuid.uuid4().hex[:24]
    memo = "HASSLE-GRAM-" + secrets.token_urlsafe(18)
    expiry = timestamp + 30 * 60
    payload = _comment_payload_boc(memo)
    with _db() as conn:
        begin_write(conn, f"gram-deposit-user:{user_id}")
        conn.execute(
            """INSERT INTO gram_deposit_intents
               (id,telegram_id,network,treasury_address,amount_nano,memo,status,created_at,expires_at)
               VALUES (?,?,?,?,?,?,'pending',?,?)""",
            (deposit_id, user_id, network, treasury, str(amount_nano), memo, timestamp, expiry),
        )
        conn.commit()
    return {
        "id": deposit_id,
        "currency": "GRAM",
        "network": network,
        "address": treasury,
        "amount": _nano_to_gram_text(amount_nano),
        "amount_nano": str(amount_nano),
        "memo": memo,
        "payload": payload,
        "expires_at": expiry,
        "status": "pending",
    }


def get_gram_deposit(telegram_id: str, deposit_id: str) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute(
            """SELECT id,amount_nano,status,created_at,expires_at,confirmed_at,tx_hash
               FROM gram_deposit_intents WHERE id=? AND telegram_id=?""",
            (str(deposit_id), str(telegram_id)),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"], "currency": "GRAM",
        "amount": _nano_to_gram_text(int(row["amount_nano"])),
        "status": row["status"], "created_at": row["created_at"],
        "expires_at": row["expires_at"], "confirmed_at": row["confirmed_at"],
        "tx_hash": row["tx_hash"],
    }


def _extract_transfer_actions(event: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    actions = event.get("actions")
    if not isinstance(actions, list):
        return []
    found = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict) or action.get("type") != "TonTransfer":
            continue
        if action.get("status") != "ok":
            continue
        transfer = action.get("TonTransfer")
        if isinstance(transfer, dict):
            found.append((index, transfer))
    return found


def _address_matches(value: Any, expected_raw: str) -> bool:
    if isinstance(value, dict):
        value = value.get("address")
    if not isinstance(value, str) or not value:
        return False
    try:
        return normalise_ton_address(value) == expected_raw
    except Exception:
        return False


def confirm_gram_deposit_event(
    *, network: str, event: dict[str, Any], action_index: int,
    transfer: dict[str, Any], now: int | None = None,
) -> bool:
    """Verify one indexed native transfer and atomically credit it once."""
    if not isinstance(event, dict) or not isinstance(transfer, dict):
        return False
    if event.get("in_progress") is not False or event.get("is_scam") is not False:
        return False
    event_id = str(event.get("event_id") or "").strip()
    tx_hash = str(event.get("event_id") or event.get("lt") or "").strip()
    if not event_id or not tx_hash:
        return False
    try:
        recipient = normalise_ton_address(str(transfer.get("recipient", {}).get("address", "")))
        amount_nano = int(str(transfer.get("amount", "")))
    except (ValueError, TypeError, AttributeError):
        return False
    if amount_nano <= 0:
        return False
    memo = str(transfer.get("comment") or "").strip()
    if not memo:
        return False
    timestamp = int(time.time()) if now is None else int(now)
    payload_hash = hashlib.sha256(
        json.dumps({"event_id": event_id, "action_index": action_index,
                    "recipient": recipient, "amount_nano": amount_nano,
                    "comment": memo}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    with _db() as conn:
        candidate = conn.execute(
            """SELECT * FROM gram_deposit_intents
               WHERE memo=? AND network=? AND status='pending'""",
            (memo, network),
        ).fetchone()
        if candidate is None:
            return False
        if recipient != candidate["treasury_address"]:
            return False
        if not _address_matches(event.get("account"), candidate["treasury_address"]):
            return False
        if amount_nano != int(candidate["amount_nano"]):
            return False
        if not _address_matches(transfer.get("recipient"), candidate["treasury_address"]):
            return False
        begin_write(conn, f"gram-deposit:{candidate['id']}", f"gram-user:{candidate['telegram_id']}")
        locked = conn.execute(
            select_for_update(conn, """SELECT * FROM gram_deposit_intents
               WHERE id=? AND status='pending'"""),
            (candidate["id"],),
        ).fetchone()
        if locked is None:
            conn.rollback()
            return False
        chain_event_id = f"{event_id}:{action_index}"
        try:
            conn.execute(
                """INSERT INTO gram_chain_events
                   (id,network,chain_event_id,action_index,tx_hash,deposit_id,amount_nano,payload_hash,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                ("gce-" + uuid.uuid4().hex[:24], network, event_id, action_index,
                 tx_hash, candidate["id"], str(amount_nano), payload_hash, timestamp),
            )
        except Exception as exc:
            conn.rollback()
            # A replay of an already processed chain event must never credit twice.
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower() or "integrity" in type(exc).__name__.lower():
                return False
            raise
        # The canonical marketplace ledger is the single source of truth for
        # available funds. GRAM is displayed externally; TON remains the protocol
        # currency key internally and amounts are converted with Decimal precision.
        from marketplace_payments import post_ledger_transaction
        post_ledger_transaction(
            conn,
            idempotency_key=f"gram-deposit:{candidate['id']}",
            reference_type="payment_intent",
            reference_id=candidate["id"],
            currency="TON",
            debit_account=("chain_clearing", candidate["treasury_address"]),
            credit_account=("user_balance", candidate["telegram_id"]),
            debit_amount=_nano_to_gram_text(amount_nano),
            credit_amount=_nano_to_gram_text(amount_nano),
            now=timestamp,
        )
        changed = conn.execute(
            """UPDATE gram_deposit_intents SET status='confirmed',confirmed_at=?,tx_hash=?,chain_event_id=?
               WHERE id=? AND status='pending'""",
            (timestamp, tx_hash, chain_event_id, candidate["id"]),
        )
        if changed.rowcount != 1:
            conn.rollback()
            return False
        conn.commit()
    _LOG.info("gram_deposit_confirmed deposit_id=%s event=%s amount_nano=%s",
              candidate["id"], event_id, amount_nano)
    return True


def _append_withdrawal_event(conn: Any, withdrawal_id: str, event_type: str,
                            event_key: str, event_data: dict[str, Any], now: int) -> None:
    conn.execute(
        """INSERT INTO gram_withdrawal_events
           (id,withdrawal_id,event_type,event_key,event_data,created_at)
           VALUES (?,?,?,?,?,?)""",
        ("gwe-" + uuid.uuid4().hex[:24], withdrawal_id, event_type, event_key,
         json.dumps(event_data, sort_keys=True, separators=(",", ":")), now),
    )


async def _require_gram_withdrawals_ready() -> tuple[str, str]:
    if os.getenv("HASSLE_GRAM_WITHDRAWALS_ENABLED", "0").strip() != "1":
        raise HTTPException(status_code=503, detail="GRAM withdrawals are temporarily unavailable")
    if os.getenv("HASSLE_TON_CUSTODY_ACKNOWLEDGED", "0").strip() != "1":
        raise HTTPException(status_code=503, detail="GRAM multisig custody policy is not acknowledged")
    if os.getenv("HASSLE_TON_PAYOUT_MODE", "").strip() != "manual_multisig_2_of_3":
        raise HTTPException(status_code=503, detail="A verified 2-of-3 GRAM payout mode is required")
    token = os.getenv("TONAPI_TOKEN", "").strip()
    try:
        network = _network()
        treasury = _treasury()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="GRAM treasury configuration is invalid") from exc
    if network != "mainnet":
        raise HTTPException(status_code=503, detail="GRAM withdrawals are currently restricted to Mainnet")
    expected = os.getenv("HASSLE_TON_MULTISIG_EXPECTED_ADDRESS", "").strip()
    if not token or not expected:
        raise HTTPException(status_code=503, detail="GRAM multisig treasury is not configured")
    try:
        if normalise_ton_address(expected) != treasury:
            raise HTTPException(status_code=503, detail="GRAM treasury and expected multisig address do not match")
        from ton_multisig import fetch_ton_multisig_config, is_two_of_three_multisig
        config = await fetch_ton_multisig_config(expected)
        if not is_two_of_three_multisig(config, expected):
            raise HTTPException(status_code=503, detail="On-chain 2-of-3 GRAM treasury verification failed")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="GRAM multisig treasury could not be verified") from exc
    return network, treasury


def _canonical_user_balance_nano(conn: Any, telegram_id: str) -> int:
    from decimal import Decimal
    rows = conn.execute(
        """SELECT direction,amount FROM marketplace_ledger_entries
           WHERE account_type='user_balance' AND account_id=? AND currency='TON'""",
        (telegram_id,),
    ).fetchall()
    total = Decimal("0")
    for row in rows:
        value = Decimal(str(row["amount"]))
        if not value.is_finite() or value <= 0:
            raise HTTPException(status_code=503, detail="GRAM balance requires reconciliation")
        total += value if row["direction"] == "credit" else -value
    scaled = total * NANO_PER_GRAM
    if total < 0 or scaled != scaled.to_integral_value():
        raise HTTPException(status_code=503, detail="GRAM balance requires reconciliation")
    return int(scaled)


def _withdrawal_dict(row: Any) -> dict[str, Any]:
    gross = int(row["gross_amount_nano"])
    fee = int(row["fee_nano"])
    net = int(row["net_amount_nano"])
    return {
        "id": row["id"], "currency": "GRAM", "network": row["network"],
        "destination_address": row["destination_address"],
        "amount": _nano_to_gram_text(gross), "fee": _nano_to_gram_text(fee),
        "net_amount": _nano_to_gram_text(net), "status": row["status"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "payout_tx_hash": row["payout_tx_hash"],
    }


async def create_gram_withdrawal(telegram_id: str, payload: GramWithdrawalRequest,
                                 now: int | None = None) -> dict[str, Any]:
    network, treasury = await _require_gram_withdrawals_ready()
    user_id = str(telegram_id).strip()
    if not user_id.isdigit():
        raise HTTPException(status_code=401, detail="Invalid Telegram user")
    try:
        from tonsdk.utils import Address
        destination_address = Address(payload.destination_address)
        if destination_address.wc != 0:
            raise ValueError("Only workchain-0 GRAM destinations are supported")
        destination = normalise_ton_address(payload.destination_address)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid GRAM destination address") from exc
    if destination == treasury:
        raise HTTPException(status_code=422, detail="Withdrawal destination cannot be the HASSLE treasury")
    try:
        gross = _amount_to_nano(payload.amount)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="GRAM withdrawal amount is outside the supported range") from exc
    try:
        fee_bps = int(os.getenv("HASSLE_GRAM_WITHDRAWAL_FEE_BPS", "0"))
        minimum = _amount_to_nano(Decimal(os.getenv("HASSLE_GRAM_MIN_WITHDRAWAL", "0.1")))
        maximum = _amount_to_nano(Decimal(os.getenv("HASSLE_GRAM_MAX_WITHDRAWAL", "10000")))
        daily_limit = _amount_to_nano(Decimal(os.getenv("HASSLE_GRAM_DAILY_WITHDRAWAL_LIMIT", "5000")))
    except (ValueError, InvalidOperation) as exc:
        raise HTTPException(status_code=503, detail="GRAM withdrawal limits are misconfigured") from exc
    if not 0 <= fee_bps <= 200 or minimum <= 0 or maximum < minimum or daily_limit < minimum:
        raise HTTPException(status_code=503, detail="GRAM withdrawal fee or limits are invalid")
    fee = (gross * fee_bps) // 10_000
    net = gross - fee
    if net <= 0:
        raise HTTPException(status_code=422, detail="Withdrawal amount is too small after fees")
    if gross < minimum or gross > maximum:
        raise HTTPException(status_code=422, detail="Withdrawal amount is outside configured limits")
    timestamp = int(time.time()) if now is None else int(now)
    day_start = timestamp - (timestamp % 86400)
    withdrawal_id = "gw-" + uuid.uuid4().hex[:24]
    with _db() as conn:
        begin_write(conn, f"gram-user:{user_id}")
        prior = conn.execute(
            "SELECT * FROM gram_withdrawals WHERE telegram_id=? AND idempotency_key=?",
            (user_id, payload.idempotency_key),
        ).fetchone()
        if prior is not None:
            conn.commit()
            return _withdrawal_dict(prior)
        daily_rows = conn.execute(
            """SELECT gross_amount_nano FROM gram_withdrawals
               WHERE telegram_id=? AND created_at>=?
                 AND status NOT IN ('cancelled','failed')""",
            (user_id, day_start),
        ).fetchall()
        daily_total = sum(int(row["gross_amount_nano"]) for row in daily_rows)
        if daily_total + gross > daily_limit:
            conn.rollback()
            raise HTTPException(status_code=429, detail="Daily GRAM withdrawal limit exceeded")
        available = _canonical_user_balance_nano(conn, user_id)
        if available < gross:
            conn.rollback()
            raise HTTPException(status_code=409, detail="Insufficient available GRAM balance")
        conn.execute(
            """INSERT INTO gram_withdrawals
               (id,telegram_id,network,destination_address,gross_amount_nano,fee_nano,
                net_amount_nano,status,idempotency_key,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,'queued_for_multisig',?,?,?)""",
            (withdrawal_id, user_id, network, destination, str(gross), str(fee),
             str(net), payload.idempotency_key, timestamp, timestamp),
        )
        from marketplace_payments import post_ledger_transaction
        post_ledger_transaction(
            conn,
            idempotency_key=f"gram-withdrawal-reserve:{withdrawal_id}",
            reference_type="withdrawal",
            reference_id=withdrawal_id,
            currency="TON",
            debit_account=("user_balance", user_id),
            credit_account=("withdrawal_reserved", withdrawal_id),
            debit_amount=_nano_to_gram_text(gross),
            credit_amount=_nano_to_gram_text(gross),
            now=timestamp,
        )
        _append_withdrawal_event(
            conn, withdrawal_id, "withdrawal_requested",
            f"{withdrawal_id}:requested",
            {"destination": destination, "gross_nano": str(gross),
             "fee_nano": str(fee), "net_nano": str(net), "network": network},
            timestamp,
        )
        conn.commit()
        row = conn.execute("SELECT * FROM gram_withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
    return _withdrawal_dict(row)


def get_gram_withdrawal(telegram_id: str, withdrawal_id: str) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM gram_withdrawals WHERE id=? AND telegram_id=?",
            (withdrawal_id, str(telegram_id)),
        ).fetchone()
    return _withdrawal_dict(row) if row is not None else None


def list_gram_withdrawals(telegram_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute(
            """SELECT * FROM gram_withdrawals WHERE telegram_id=?
               ORDER BY created_at DESC LIMIT ?""",
            (str(telegram_id), max(1, min(int(limit), 50))),
        ).fetchall()
    return [_withdrawal_dict(row) for row in rows]


def cancel_gram_withdrawal(telegram_id: str, withdrawal_id: str,
                           now: int | None = None) -> dict[str, Any]:
    user_id = str(telegram_id)
    timestamp = int(time.time()) if now is None else int(now)
    with _db() as conn:
        begin_write(conn, f"gram-user:{user_id}", f"gram-withdrawal:{withdrawal_id}")
        row = conn.execute(
            select_for_update(conn, """SELECT * FROM gram_withdrawals
               WHERE id=? AND telegram_id=?"""),
            (withdrawal_id, user_id),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Withdrawal not found")
        if row["status"] == "cancelled":
            conn.rollback()
            return _withdrawal_dict(row)
        if row["status"] != "queued_for_multisig":
            conn.rollback()
            raise HTTPException(status_code=409, detail="Withdrawal can no longer be cancelled")
        from marketplace_payments import post_ledger_transaction
        amount = _nano_to_gram_text(int(row["gross_amount_nano"]))
        post_ledger_transaction(
            conn,
            idempotency_key=f"gram-withdrawal-release:{withdrawal_id}",
            reference_type="withdrawal",
            reference_id=withdrawal_id,
            currency="TON",
            debit_account=("withdrawal_reserved", withdrawal_id),
            credit_account=("user_balance", user_id),
            debit_amount=amount,
            credit_amount=amount,
            now=timestamp,
        )
        conn.execute(
            "UPDATE gram_withdrawals SET status='cancelled',updated_at=? WHERE id=? AND status='queued_for_multisig'",
            (timestamp, withdrawal_id),
        )
        _append_withdrawal_event(
            conn, withdrawal_id, "withdrawal_cancelled",
            f"{withdrawal_id}:cancelled",
            {"released_nano": str(row["gross_amount_nano"])}, timestamp,
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM gram_withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
    return _withdrawal_dict(updated)


async def poll_gram_deposits_once(client: httpx.AsyncClient | None = None) -> dict[str, int]:
    if os.getenv("HASSLE_GRAM_DEPOSITS_ENABLED", "0").strip() != "1":
        return {"scanned": 0, "credited": 0}
    token = os.getenv("TONAPI_TOKEN", "").strip()
    try:
        network = _network()
        treasury = _treasury()
    except Exception:
        _LOG.exception("GRAM indexer configuration is invalid")
        return {"scanned": 0, "credited": 0}
    if not token:
        return {"scanned": 0, "credited": 0}
    base = "https://tonapi.io" if network == "mainnet" else "https://testnet.tonapi.io"
    url = f"{base}/v2/accounts/{treasury}/events"
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=20.0)
    try:
        response = await http.get(url, headers={"Authorization": f"Bearer {token}"},
                                  params={"limit": 100, "subject_only": "true"})
        response.raise_for_status()
        data = response.json()
        events = data.get("events", []) if isinstance(data, dict) else []
        scanned = credited = 0
        for event in events:
            if not isinstance(event, dict):
                continue
            for index, transfer in _extract_transfer_actions(event):
                scanned += 1
                memo = str(transfer.get("comment") or "").strip()
                if not memo.startswith("HASSLE-GRAM-"):
                    continue
                try:
                    if confirm_gram_deposit_event(network=network, event=event,
                                                  action_index=index, transfer=transfer):
                        credited += 1
                except Exception:
                    _LOG.exception("GRAM deposit event processing failed")
        return {"scanned": scanned, "credited": credited}
    finally:
        if own_client:
            await http.aclose()


async def run_gram_deposit_indexer() -> None:
    while True:
        try:
            result = await poll_gram_deposits_once()
            if result["credited"]:
                _LOG.info("gram_indexer_batch=%s", json.dumps(result, sort_keys=True))
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception("GRAM deposit indexer iteration failed")
        await asyncio.sleep(max(5, int(os.getenv("HASSLE_GRAM_INDEXER_INTERVAL_SECONDS", "15"))))


def create_gram_deposit_router(authenticate, rate_limit) -> APIRouter:
    router = APIRouter(prefix="/api/gram", tags=["GRAM payments"])

    @router.post("/deposits")
    async def create_deposit(payload: GramDepositRequest, authorization: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        if not authorization:
            raise HTTPException(status_code=401, detail="Telegram Mini App authentication required")
        user = authenticate(authorization)
        user_id = str(user["id"])
        rate_limit(user_id, "gram_deposit_create", 5, 60)
        try:
            network, treasury, token = _require_deposits_enabled()
            await _verify_active_gram_treasury(network, treasury, token)
            return create_gram_deposit(user_id, payload.amount)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="GRAM deposits are not configured") from exc

    @router.get("/deposits/{deposit_id}")
    def read_deposit(deposit_id: str, authorization: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        if not authorization:
            raise HTTPException(status_code=401, detail="Telegram Mini App authentication required")
        user = authenticate(authorization)
        result = get_gram_deposit(str(user["id"]), deposit_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Deposit not found")
        return result

    @router.post("/withdrawals")
    async def create_withdrawal(payload: GramWithdrawalRequest,
                                authorization: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        if not authorization:
            raise HTTPException(status_code=401, detail="Telegram Mini App authentication required")
        user = authenticate(authorization)
        user_id = str(user["id"])
        rate_limit(user_id, "gram_withdrawal_create", 3, 3600)
        return await create_gram_withdrawal(user_id, payload)

    @router.get("/withdrawals")
    def read_withdrawals(limit: int = 20,
                         authorization: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        if not authorization:
            raise HTTPException(status_code=401, detail="Telegram Mini App authentication required")
        user = authenticate(authorization)
        return {"items": list_gram_withdrawals(str(user["id"]), limit)}

    @router.get("/withdrawals/{withdrawal_id}")
    def read_withdrawal(withdrawal_id: str,
                        authorization: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        if not authorization:
            raise HTTPException(status_code=401, detail="Telegram Mini App authentication required")
        user = authenticate(authorization)
        result = get_gram_withdrawal(str(user["id"]), withdrawal_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Withdrawal not found")
        return result

    @router.post("/withdrawals/{withdrawal_id}/cancel")
    def cancel_withdrawal(withdrawal_id: str,
                          authorization: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        if not authorization:
            raise HTTPException(status_code=401, detail="Telegram Mini App authentication required")
        user = authenticate(authorization)
        rate_limit(str(user["id"]), "gram_withdrawal_cancel", 5, 3600)
        return cancel_gram_withdrawal(str(user["id"]), withdrawal_id)

    @router.get("/balance")
    def read_balance(authorization: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        if not authorization:
            raise HTTPException(status_code=401, detail="Telegram Mini App authentication required")
        user = authenticate(authorization)
        from decimal import Decimal
        with _db() as conn:
            rows = conn.execute(
                """SELECT le.account_type,le.direction,le.amount
                   FROM marketplace_ledger_entries le
                   LEFT JOIN gram_withdrawals gw
                     ON le.account_type='withdrawal_reserved' AND le.account_id=gw.id
                   WHERE le.currency='TON' AND (
                     (le.account_type='user_balance' AND le.account_id=?) OR
                     (le.account_type='withdrawal_reserved' AND gw.telegram_id=?)
                   )""",
                (str(user["id"]), str(user["id"])),
            ).fetchall()
        available_gram = Decimal("0")
        reserved_gram = Decimal("0")
        for row in rows:
            amount = Decimal(str(row["amount"]))
            if not amount.is_finite() or amount <= 0:
                raise HTTPException(status_code=503, detail="GRAM balance requires reconciliation")
            delta = amount if row["direction"] == "credit" else -amount
            if row["account_type"] == "user_balance":
                available_gram += delta
            else:
                reserved_gram += delta
        if available_gram < 0 or reserved_gram < 0:
            raise HTTPException(status_code=503, detail="GRAM balance requires reconciliation")
        available_scaled = available_gram * NANO_PER_GRAM
        reserved_scaled = reserved_gram * NANO_PER_GRAM
        if available_scaled != available_scaled.to_integral_value() or reserved_scaled != reserved_scaled.to_integral_value():
            raise HTTPException(status_code=503, detail="GRAM balance precision requires reconciliation")
        available = int(available_scaled)
        reserved = int(reserved_scaled)
        return {"currency": "GRAM", "available_nano": str(available),
                "reserved_nano": str(reserved), "available": _nano_to_gram_text(available),
                "reserved": _nano_to_gram_text(reserved)}

    return router
