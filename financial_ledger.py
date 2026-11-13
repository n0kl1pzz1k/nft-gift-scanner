"""Append-only double-entry ledger and fail-closed TON trace verification."""
from __future__ import annotations
import hashlib
import json
import os
import time
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger, CheckConstraint, Column, ForeignKey, Integer, MetaData, String,
    Table, UniqueConstraint, create_engine, insert, select, text,
)
from sqlalchemy.engine import Connection, Engine

metadata = MetaData()
accounts = Table("ledger_accounts", metadata,
    Column("account_id", String(160), primary_key=True),
    Column("account_type", String(32), nullable=False),
    Column("currency", String(16), nullable=False),
    Column("created_at", BigInteger, nullable=False))
entries = Table("ledger_entries", metadata,
    Column("entry_id", String(36), primary_key=True),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("source_type", String(40), nullable=False),
    Column("source_reference", String(200), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("created_at", BigInteger, nullable=False))
lines = Table("ledger_lines", metadata,
    Column("line_id", Integer, primary_key=True, autoincrement=True),
    Column("entry_id", String(36), ForeignKey("ledger_entries.entry_id"), nullable=False),
    Column("account_id", String(160), ForeignKey("ledger_accounts.account_id"), nullable=False),
    Column("currency", String(16), nullable=False),
    Column("amount_raw", String(80), nullable=False),
    CheckConstraint("amount_raw != '0'", name="ck_ledger_line_nonzero"))
invoices = Table("payment_invoices", metadata,
    Column("invoice_id", String(64), primary_key=True),
    Column("user_id", String(64), nullable=False),
    Column("asset_id", String(32), nullable=False),
    Column("chain", String(32), nullable=False),
    Column("master_address", String(128), nullable=False),
    Column("recipient_address", String(128), nullable=False),
    Column("amount_raw", String(80), nullable=False),
    Column("payload", String(256), nullable=False, unique=True),
    Column("expires_at", BigInteger, nullable=False),
    Column("status", String(24), nullable=False),
    Column("created_at", BigInteger, nullable=False))
payment_events = Table("payment_events", metadata,
    Column("payment_event_id", String(36), primary_key=True),
    Column("invoice_id", String(64), ForeignKey("payment_invoices.invoice_id"), nullable=False, unique=True),
    Column("chain", String(32), nullable=False),
    Column("tx_hash", String(128), nullable=False),
    Column("event_index", BigInteger, nullable=False),
    Column("asset_id", String(32), nullable=False),
    Column("amount_raw", String(80), nullable=False),
    Column("recipient_address", String(128), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    UniqueConstraint("chain", "tx_hash", "event_index", "asset_id", name="uq_payment_chain_event"))

_engine: Engine | None = None

def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        if os.getenv("ENVIRONMENT", "development").lower() == "production":
            raise RuntimeError("DATABASE_URL is required in production; financial storage unavailable")
        os.makedirs("./data", exist_ok=True)
        url = "sqlite:///./data/gift-radar-finance.sqlite3"
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url

def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = _database_url()
        _engine = create_engine(url, future=True, pool_pre_ping=True,
            connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {})
    return _engine

def init_financial_schema(engine: Engine | None = None) -> None:
    db = engine or get_engine()
    if db.dialect.name == "postgresql":
        with db.connect() as conn:
            if not db.dialect.has_table(conn, "alembic_version"):
                raise RuntimeError("PostgreSQL schema must be initialized through Alembic migrations")
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
            if version != "0001_financial_ledger":
                raise RuntimeError("Financial database migration is missing or not at the expected revision")
        return
    metadata.create_all(db)
    if db.dialect.name == "sqlite":
        with db.begin() as conn:
            for table in ("ledger_entries", "ledger_lines", "payment_events"):
                for action in ("UPDATE", "DELETE"):
                    conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'append-only financial record'); END"))

def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

def _ensure_account(conn: Connection, account_id: str, account_type: str, currency: str, now: int) -> None:
    row = conn.execute(select(accounts.c.currency).where(accounts.c.account_id == account_id)).first()
    if row is None:
        conn.execute(insert(accounts).values(account_id=account_id, account_type=account_type, currency=currency, created_at=now))
    elif row.currency != currency:
        raise ValueError("Ledger account currency is immutable")

def _post(conn: Connection, *, idempotency_key: str, source_type: str, source_reference: str,
          postings: list[dict[str, Any]], now: int) -> dict[str, Any]:
    if not idempotency_key or len(idempotency_key) > 200 or len(postings) < 2:
        raise ValueError("A bounded idempotency key and at least two postings are required")
    normalized, totals = [], {}
    for p in postings:
        item = {"account_id": str(p["account_id"]).strip(), "account_type": str(p["account_type"]).strip(),
                "currency": str(p["currency"]).upper().strip(), "amount_raw": int(p["amount_raw"])}
        if not item["account_id"] or not item["currency"] or item["amount_raw"] == 0:
            raise ValueError("Invalid ledger posting")
        normalized.append(item)
        totals[item["currency"]] = totals.get(item["currency"], 0) + item["amount_raw"]
    if any(total != 0 for total in totals.values()):
        raise ValueError("Unbalanced journal entry")
    digest = _hash({"source_type": source_type, "source_reference": source_reference,
                    "postings": sorted(normalized, key=lambda x: (x["account_id"], x["currency"], x["amount_raw"]))})
    existing = conn.execute(select(entries.c.entry_id, entries.c.payload_hash).where(entries.c.idempotency_key == idempotency_key)).first()
    if existing:
        if existing.payload_hash != digest:
            raise ValueError("Idempotency key reused with different financial payload")
        return {"entry_id": existing.entry_id, "duplicate": True}
    entry_id = str(uuid.uuid4())
    conn.execute(insert(entries).values(entry_id=entry_id, idempotency_key=idempotency_key,
        source_type=source_type, source_reference=source_reference, payload_hash=digest, created_at=now))
    for p in normalized:
        _ensure_account(conn, p["account_id"], p["account_type"], p["currency"], now)
        conn.execute(insert(lines).values(entry_id=entry_id, account_id=p["account_id"],
            currency=p["currency"], amount_raw=str(p["amount_raw"])))
    return {"entry_id": entry_id, "duplicate": False}

def post_journal_entry(*, idempotency_key: str, source_type: str, source_reference: str,
                       postings: list[dict[str, Any]], engine: Engine | None = None) -> dict[str, Any]:
    """Post balanced integer-unit lines. There is deliberately no balance setter."""
    db = engine or get_engine()
    init_financial_schema(db)
    try:
        with db.begin() as conn:
            return _post(conn, idempotency_key=idempotency_key, source_type=source_type,
                         source_reference=source_reference, postings=postings, now=int(time.time()))
    except Exception as exc:
        # A concurrent unique-key race is resolved only after the failed transaction rolls back.
        from sqlalchemy.exc import IntegrityError
        if not isinstance(exc, IntegrityError):
            raise
        candidate = _hash({"source_type": source_type, "source_reference": source_reference,
            "postings": sorted(
                [{"account_id": str(p["account_id"]).strip(), "account_type": str(p["account_type"]).strip(),
                  "currency": str(p["currency"]).upper().strip(), "amount_raw": int(p["amount_raw"])}
                 for p in postings],
                key=lambda x: (x["account_id"], x["currency"], x["amount_raw"]))})
        with db.connect() as conn:
            existing = conn.execute(select(entries.c.entry_id, entries.c.payload_hash)
                .where(entries.c.idempotency_key == idempotency_key)).first()
        if existing and existing.payload_hash == candidate:
            return {"entry_id": existing.entry_id, "duplicate": True}
        if existing:
            raise ValueError("Idempotency key reused with different financial payload") from exc
        raise

def get_account_balance(account_id: str, currency: str, engine: Engine | None = None) -> int:
    db = engine or get_engine()
    init_financial_schema(db)
    with db.connect() as conn:
        values = conn.execute(select(lines.c.amount_raw).where(
            lines.c.account_id == account_id, lines.c.currency == currency.upper())).scalars()
        return sum(int(value) for value in values)

def create_payment_invoice(*, user_id: str, asset_id: str, chain: str, master_address: str,
    recipient_address: str, amount_raw: int, payload: str, expires_at: int,
    engine: Engine | None = None) -> str:
    """Server-owned invoice creation. Crypto rails are disabled until policy approval."""
    from asset_policy import get_asset, is_crypto_payment_enabled
    asset = get_asset(asset_id, chain, master_address, "external")
    if not is_crypto_payment_enabled(asset_id):
        raise PermissionError("Crypto payment rail is disabled by compliance policy")
    if amount_raw <= 0 or expires_at <= int(time.time()) or not payload:
        raise ValueError("Invalid payment invoice")
    import secrets
    invoice_id = "gri_" + secrets.token_urlsafe(24)
    db = engine or get_engine()
    init_financial_schema(db)
    with db.begin() as conn:
        conn.execute(insert(invoices).values(invoice_id=invoice_id, user_id=str(user_id),
            asset_id=asset.asset_id, chain=asset.chain.value, master_address=asset.master_address,
            recipient_address=recipient_address, amount_raw=str(amount_raw), payload=payload,
            expires_at=expires_at, status="pending", created_at=int(time.time())))
    return invoice_id

def verify_ton_payment_trace(trace: dict[str, Any], invoice: dict[str, Any]) -> None:
    """Validate normalized evidence from a trusted indexer, never from a browser."""
    required = ("chain", "master_address", "source_jetton_wallet", "expected_source_jetton_wallet",
        "recipient_address", "amount_raw", "payload", "opcode", "successful", "bounced",
        "finalized", "tx_hash", "event_index")
    if any(k not in trace for k in required):
        raise ValueError("Incomplete on-chain evidence")
    if trace["chain"] != "ton-mainnet" or trace["chain"] != invoice["chain"]:
        raise ValueError("Wrong blockchain network")
    if trace["master_address"] != invoice["master_address"]:
        raise ValueError("Token master mismatch")
    if trace["source_jetton_wallet"] != trace["expected_source_jetton_wallet"]:
        raise ValueError("Sender is not the canonical Jetton wallet")
    if trace["recipient_address"] != invoice["recipient_address"]:
        raise ValueError("Payment recipient mismatch")
    if int(trace["amount_raw"]) != int(invoice["amount_raw"]):
        raise ValueError("Payment amount mismatch")
    if trace["payload"] != invoice["payload"]:
        raise ValueError("Invoice payload mismatch")
    if str(trace["opcode"]).lower() not in {"0x7362d09c", "1935855772"}:
        raise ValueError("Unexpected Jetton transfer notification opcode")
    if trace["successful"] is not True or trace["bounced"] is not False or trace["finalized"] is not True:
        raise ValueError("Transaction unsuccessful, bounced, or not finalized")
    if not trace["tx_hash"] or int(trace["event_index"]) < 0:
        raise ValueError("Invalid chain event identity")

def settle_verified_ton_payment(*, invoice_id: str, trace: dict[str, Any],
                                engine: Engine | None = None) -> dict[str, Any]:
    """Atomically deduplicate a verified chain event and post the ledger entry."""
    db = engine or get_engine()
    init_financial_schema(db)
    with db.begin() as conn:
        row = conn.execute(select(invoices).where(invoices.c.invoice_id == invoice_id)).mappings().first()
        if row is None:
            raise ValueError("Unknown payment invoice")
        invoice = dict(row)
        verify_ton_payment_trace(trace, invoice)
        duplicate = conn.execute(select(payment_events.c.payment_event_id).where(
            payment_events.c.chain == trace["chain"], payment_events.c.tx_hash == trace["tx_hash"],
            payment_events.c.event_index == int(trace["event_index"]),
            payment_events.c.asset_id == invoice["asset_id"])).first()
        if duplicate:
            return {"credited": False, "duplicate": True}
        if invoice["status"] != "pending" or int(invoice["expires_at"]) < int(time.time()):
            raise ValueError("Invoice is not payable")
        event_id = str(uuid.uuid4())
        conn.execute(insert(payment_events).values(payment_event_id=event_id, invoice_id=invoice_id,
            chain=trace["chain"], tx_hash=trace["tx_hash"], event_index=int(trace["event_index"]),
            asset_id=invoice["asset_id"], amount_raw=str(int(trace["amount_raw"])),
            recipient_address=trace["recipient_address"], created_at=int(time.time())))
        result = _post(conn, idempotency_key=f"chain:{trace['chain']}:{trace['tx_hash']}:{trace['event_index']}:{invoice['asset_id']}",
            source_type="verified_chain_payment", source_reference=invoice_id,
            postings=[
                {"account_id": f"asset:clearing:{invoice['asset_id']}", "account_type": "asset",
                 "currency": invoice["asset_id"], "amount_raw": int(trace["amount_raw"])},
                {"account_id": f"liability:user:{invoice['user_id']}:{invoice['asset_id']}",
                 "account_type": "liability", "currency": invoice["asset_id"],
                 "amount_raw": -int(trace["amount_raw"])},
            ], now=int(time.time()))
        conn.execute(invoices.update().where(invoices.c.invoice_id == invoice_id).values(status="paid"))
        return {"credited": True, "duplicate": False, "entry_id": result["entry_id"], "event_id": event_id}
