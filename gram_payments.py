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
            """CREATE TABLE IF NOT EXISTS gram_wallet_balances (
                telegram_id TEXT PRIMARY KEY,
                available_nano TEXT NOT NULL DEFAULT '0',
                reserved_nano TEXT NOT NULL DEFAULT '0',
                updated_at BIGINT NOT NULL
            )""",
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
            """CREATE TABLE IF NOT EXISTS gram_ledger_transactions (
                id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                reference_type TEXT NOT NULL CHECK(reference_type IN ('deposit')),
                reference_id TEXT NOT NULL,
                amount_nano TEXT NOT NULL,
                created_at BIGINT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS gram_ledger_entries (
                id TEXT PRIMARY KEY,
                transaction_id TEXT NOT NULL,
                account_type TEXT NOT NULL CHECK(account_type IN ('chain_clearing','user_available')),
                account_id TEXT NOT NULL,
                direction TEXT NOT NULL CHECK(direction IN ('debit','credit')),
                amount_nano TEXT NOT NULL,
                created_at BIGINT NOT NULL,
                UNIQUE(transaction_id, account_type, account_id, direction)
            )""",
        )
        for statement in statements:
            conn.execute(statement)
        # Append-only journal protection is installed on SQLite. PostgreSQL
        # writes are restricted to this service role and remain append-only by API.
        if conn.__class__.__module__ == "sqlite3":
            for table in ("gram_chain_events", "gram_ledger_transactions", "gram_ledger_entries"):
                conn.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_update
                    BEFORE UPDATE ON {table}
                    BEGIN SELECT RAISE(ABORT, 'GRAM financial journal is immutable'); END""")
                conn.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_delete
                    BEFORE DELETE ON {table}
                    BEGIN SELECT RAISE(ABORT, 'GRAM financial journal is immutable'); END""")


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
    if event.get("in_progress") is not False or event.get("is_scam") is True:
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
               WHERE memo=? AND network=? AND status='pending' AND expires_at>=?""",
            (memo, network, timestamp),
        ).fetchone()
        if candidate is None:
            return False
        if recipient != candidate["treasury_address"]:
            return False
        if amount_nano != int(candidate["amount_nano"]):
            return False
        if not _address_matches(transfer.get("recipient"), candidate["treasury_address"]):
            return False
        if str(event.get("in_progress")) != "False":
            return False
        if event.get("is_scam") not in (False, None):
            return False
        begin_write(conn, f"gram-deposit:{candidate['id']}", f"gram-user:{candidate['telegram_id']}")
        locked = conn.execute(
            select_for_update(conn, """SELECT * FROM gram_deposit_intents
               WHERE id=? AND status='pending' AND expires_at>=?"""),
            (candidate["id"], timestamp),
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
        balance = conn.execute(
            select_for_update(conn, "SELECT available_nano,reserved_nano FROM gram_wallet_balances WHERE telegram_id=?"),
            (candidate["telegram_id"],),
        ).fetchone()
        if balance is None:
            conn.execute(
                """INSERT INTO gram_wallet_balances(telegram_id,available_nano,reserved_nano,updated_at)
                   VALUES (?, '0','0',?)""",
                (candidate["telegram_id"], timestamp),
            )
            available = 0
        else:
            available = int(balance["available_nano"])
        new_available = available + amount_nano
        ledger_id = "glt-" + uuid.uuid4().hex[:24]
        conn.execute(
            """INSERT INTO gram_ledger_transactions
               (id,idempotency_key,reference_type,reference_id,amount_nano,created_at)
               VALUES (?,?, 'deposit',?,?,?)""",
            (ledger_id, f"gram-deposit:{candidate['id']}", candidate["id"], str(amount_nano), timestamp),
        )
        conn.executemany(
            """INSERT INTO gram_ledger_entries
               (id,transaction_id,account_type,account_id,direction,amount_nano,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            [
                ("gle-" + uuid.uuid4().hex[:24], ledger_id, "chain_clearing",
                 candidate["treasury_address"], "debit", str(amount_nano), timestamp),
                ("gle-" + uuid.uuid4().hex[:24], ledger_id, "user_available",
                 candidate["telegram_id"], "credit", str(amount_nano), timestamp),
            ],
        )
        conn.execute(
            """INSERT INTO gram_wallet_balances(telegram_id,available_nano,reserved_nano,updated_at)
               VALUES (?,?,'0',?)
               ON CONFLICT(telegram_id) DO UPDATE SET
                 available_nano=excluded.available_nano,updated_at=excluded.updated_at""",
            (candidate["telegram_id"], str(new_available), timestamp),
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
    def create_deposit(payload: GramDepositRequest, authorization: str | None = Header(default=None)):
        if not authorization:
            raise HTTPException(status_code=401, detail="Telegram Mini App authentication required")
        user = authenticate(authorization)
        user_id = str(user["id"])
        rate_limit(user_id, "gram_deposit_create", 5, 60)
        try:
            return create_gram_deposit(user_id, payload.amount)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="GRAM deposits are not configured") from exc

    @router.get("/deposits/{deposit_id}")
    def read_deposit(deposit_id: str, authorization: str | None = Header(default=None)):
        if not authorization:
            raise HTTPException(status_code=401, detail="Telegram Mini App authentication required")
        user = authenticate(authorization)
        result = get_gram_deposit(str(user["id"]), deposit_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Deposit not found")
        return result

    @router.get("/balance")
    def read_balance(authorization: str | None = Header(default=None)):
        if not authorization:
            raise HTTPException(status_code=401, detail="Telegram Mini App authentication required")
        user = authenticate(authorization)
        with _db() as conn:
            row = conn.execute(
                "SELECT available_nano,reserved_nano FROM gram_wallet_balances WHERE telegram_id=?",
                (str(user["id"]),),
            ).fetchone()
        available = int(row["available_nano"]) if row else 0
        reserved = int(row["reserved_nano"]) if row else 0
        return {"currency": "GRAM", "available_nano": str(available),
                "reserved_nano": str(reserved), "available": _nano_to_gram_text(available),
                "reserved": _nano_to_gram_text(reserved)}

    return router
