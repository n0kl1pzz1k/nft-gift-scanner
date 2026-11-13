"""Append-only double-entry ledger and fail-closed TON trace verification."""
from __future__ import annotations
import hashlib
import json
import os
import secrets
import time
from decimal import Decimal, InvalidOperation
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger, CheckConstraint, Column, ForeignKey, Integer, MetaData, String,
    Index, Table, UniqueConstraint, create_engine, insert, select, text,
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
stars_invoices = Table("telegram_stars_invoices", metadata,
    Column("invoice_id", String(64), primary_key=True),
    Column("payload", String(128), nullable=False, unique=True),
    Column("deal_id", String(64), nullable=False),
    Column("order_id", String(64), nullable=False),
    Column("buyer_user_id", String(64), nullable=False),
    Column("seller_user_id", String(64), nullable=False),
    Column("amount_stars", BigInteger, nullable=False),
    Column("currency", String(8), nullable=False),
    Column("status", String(32), nullable=False),
    Column("expires_at", BigInteger, nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("pre_checkout_query_id", String(128), unique=True),
    Column("pre_checkout_approved_at", BigInteger),
    Column("paid_at", BigInteger),
    Column("telegram_payment_charge_id", String(256), unique=True),
    Column("provider_payment_charge_id", String(256)),
    Column("refunded_at", BigInteger),
    CheckConstraint("amount_stars > 0", name="ck_telegram_stars_invoice_amount"),
    CheckConstraint("currency = 'XTR'", name="ck_telegram_stars_invoice_currency"),
    CheckConstraint("status IN ('pending', 'pre_checkout_approved', 'paid', 'expired', 'refunded')", name="ck_telegram_stars_invoice_status"))
Index("uq_telegram_stars_open_invoice_per_deal", stars_invoices.c.deal_id, unique=True,
      sqlite_where=text("status IN ('pending', 'pre_checkout_approved')"),
      postgresql_where=text("status IN ('pending', 'pre_checkout_approved')"))
stars_payments = Table("telegram_stars_payments", metadata,
    Column("payment_event_id", String(36), primary_key=True),
    Column("invoice_id", String(64), ForeignKey("telegram_stars_invoices.invoice_id"), nullable=False, unique=True),
    Column("telegram_payment_charge_id", String(256), nullable=False, unique=True),
    Column("buyer_user_id", String(64), nullable=False),
    Column("amount_stars", BigInteger, nullable=False),
    Column("currency", String(8), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    CheckConstraint("amount_stars > 0", name="ck_telegram_stars_payment_amount"),
    CheckConstraint("currency = 'XTR'", name="ck_telegram_stars_payment_currency"))
stars_refund_events = Table("telegram_stars_refund_events", metadata,
    Column("refund_event_id", String(36), primary_key=True),
    Column("invoice_id", String(64), ForeignKey("telegram_stars_invoices.invoice_id"), nullable=False),
    Column("telegram_payment_charge_id", String(256), nullable=False),
    Column("event_type", String(32), nullable=False),
    Column("reason", String(500), nullable=False, default=""),
    Column("created_at", BigInteger, nullable=False),
    CheckConstraint("event_type IN ('refund_requested', 'refund_succeeded', 'refund_failed')", name="ck_telegram_stars_refund_event_type"))

_engine: Engine | None = None

def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    storage_backend = os.getenv("GIFT_RADAR_STORAGE_BACKEND", "sqlite").strip().lower()
    if not url:
        if os.getenv("ENVIRONMENT", "development").lower() == "production" or storage_backend == "postgres":
            raise RuntimeError("DATABASE_URL is required for production/shared financial storage")
        database_path = os.getenv("GIFT_RADAR_DB_PATH", "./data/gift-radar.sqlite3").strip()
        os.makedirs(os.path.dirname(database_path) or ".", exist_ok=True)
        url = "sqlite:///" + database_path
    is_postgres_url = url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://"))
    if storage_backend == "postgres" and not is_postgres_url:
        raise RuntimeError("PostgreSQL storage was selected but DATABASE_URL is not PostgreSQL")
    if storage_backend != "postgres" and is_postgres_url:
        raise RuntimeError("Financial and marketplace stores must use the same configured database backend")
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
            if version not in {"0001_financial_ledger", "0002_marketplace_core", "0003_telegram_stars_payments"}:
                raise RuntimeError("Financial database migration is missing or not at the expected revision")
        return
    metadata.create_all(db)
    if db.dialect.name == "sqlite":
        with db.begin() as conn:
            for table in ("ledger_entries", "ledger_lines", "payment_events", "telegram_stars_payments", "telegram_stars_refund_events"):
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

def create_telegram_stars_invoice(*, deal_id: str, buyer_user_id: str,
                                  engine: Engine | None = None, now: int | None = None,
                                  ttl_seconds: int = 900) -> dict[str, Any]:
    """Create/reuse a server-owned XTR invoice for the buyer of an accepted deal."""
    db = engine or get_engine()
    init_financial_schema(db)
    current = int(now or time.time())
    if os.getenv("MARKETPLACE_REAL_DEALS_ENABLED", "false").strip().lower() != "true":
        raise PermissionError("Real marketplace fulfilment is not enabled")
    if ttl_seconds < 60 or ttl_seconds > 1800:
        raise ValueError("Invoice lifetime must be between 60 and 1800 seconds")
    lock = " FOR UPDATE OF d, o" if db.dialect.name == "postgresql" else ""
    query = (
        "SELECT d.id AS deal_id, d.order_id, d.listing_owner_id, d.offer_sender_id, "
        "d.agreed_amount, d.currency, d.is_real, d.status AS deal_status, o.kind AS order_kind, "
        "o.status AS order_status, o.title AS order_title "
        "FROM demo_deals d JOIN demo_orders o ON o.id=d.order_id WHERE d.id=:deal_id" + lock
    )
    with db.begin() as conn:
        deal = conn.execute(text(query), {"deal_id": str(deal_id)}).mappings().first()
        if deal is None:
            raise ValueError("Unknown deal")
        if int(deal["is_real"] or 0) != 1:
            raise PermissionError("Demo deals cannot be charged")
        if deal["order_status"] != "matched":
            raise ValueError("Order is no longer reserved for this deal")
        if deal["currency"] != "XTR":
            raise PermissionError("Only Telegram Stars invoices are allowed for in-Telegram digital goods")
        if deal["deal_status"] not in {"payment_not_enabled", "awaiting_payment"}:
            raise ValueError("Deal is not awaiting payment")

        if deal["order_kind"] == "listing":
            expected_buyer, seller = str(deal["offer_sender_id"]), str(deal["listing_owner_id"])
        elif deal["order_kind"] == "buy-order":
            expected_buyer, seller = str(deal["listing_owner_id"]), str(deal["offer_sender_id"])
        else:
            raise ValueError("Unsupported order type")
        if str(buyer_user_id) != expected_buyer:
            raise PermissionError("Only the deal buyer can create its payment invoice")
        if not seller or seller == expected_buyer:
            raise ValueError("Invalid deal participants")

        try:
            amount_decimal = Decimal(str(deal["agreed_amount"]))
        except (InvalidOperation, ValueError):
            raise ValueError("Deal amount is not a valid Stars amount") from None
        if not amount_decimal.is_finite() or amount_decimal <= 0 or amount_decimal != amount_decimal.to_integral_value():
            raise ValueError("Telegram Stars amount must be a positive integer")
        amount = int(amount_decimal)
        if amount > 2_147_483_647:
            raise ValueError("Telegram Stars amount exceeds the supported integer range")

        existing = conn.execute(select(stars_invoices).where(
            stars_invoices.c.deal_id == str(deal_id),
            stars_invoices.c.status.in_(("pending", "pre_checkout_approved")),
        ).order_by(stars_invoices.c.created_at.desc())).mappings().first()
        if existing and int(existing["expires_at"]) > current:
            return {**dict(existing), "title": str(deal["order_title"])[:32]}
        if existing:
            conn.execute(stars_invoices.update().where(
                stars_invoices.c.invoice_id == existing["invoice_id"]
            ).values(status="expired"))
            conn.execute(text(
                "UPDATE demo_deals SET status='payment_not_enabled' "
                "WHERE id=:deal_id AND status='awaiting_payment'"
            ), {"deal_id": str(deal_id)})

        invoice_id = "gri_" + secrets.token_urlsafe(18)
        payload = "grstars:" + secrets.token_urlsafe(24)
        expires_at = current + ttl_seconds
        conn.execute(insert(stars_invoices).values(
            invoice_id=invoice_id, payload=payload, deal_id=str(deal_id),
            order_id=str(deal["order_id"]), buyer_user_id=expected_buyer,
            seller_user_id=seller, amount_stars=amount, currency="XTR",
            status="pending", expires_at=expires_at, created_at=current,
        ))
        conn.execute(text(
            "UPDATE demo_deals SET status='awaiting_payment' "
            "WHERE id=:deal_id AND status='payment_not_enabled'"
        ), {"deal_id": str(deal_id)})
        row = conn.execute(select(stars_invoices).where(
            stars_invoices.c.invoice_id == invoice_id)).mappings().one()
        return {**dict(row), "title": str(deal["order_title"])[:32]}


def approve_telegram_stars_pre_checkout(*, payload: str, buyer_user_id: str,
                                        currency: str, amount_stars: int,
                                        query_id: str, engine: Engine | None = None,
                                        now: int | None = None) -> bool:
    """Validate and atomically reserve a Stars invoice for one Telegram pre-checkout query."""
    db = engine or get_engine()
    init_financial_schema(db)
    current = int(now or time.time())
    if (os.getenv("MARKETPLACE_REAL_DEALS_ENABLED", "false").strip().lower() != "true" or currency != "XTR" or int(amount_stars) <= 0 or not query_id or len(query_id) > 128):
        return False
    statement = select(stars_invoices).where(stars_invoices.c.payload == payload)
    if db.dialect.name == "postgresql":
        statement = statement.with_for_update()
    with db.begin() as conn:
        row = conn.execute(statement).mappings().first()
        if row is None:
            return False
        invoice = dict(row)
        if invoice["status"] == "pre_checkout_approved":
            return (
                invoice["pre_checkout_query_id"] == query_id
                and invoice["buyer_user_id"] == str(buyer_user_id)
                and invoice["currency"] == currency
                and int(invoice["amount_stars"]) == int(amount_stars)
            )
        if (invoice["status"] != "pending" or int(invoice["expires_at"]) <= current
                or invoice["buyer_user_id"] != str(buyer_user_id)
                or invoice["currency"] != currency
                or int(invoice["amount_stars"]) != int(amount_stars)):
            return False
        deal = conn.execute(text(
            "SELECT d.status AS deal_status, d.is_real, o.status AS order_status "
            "FROM demo_deals d JOIN demo_orders o ON o.id=d.order_id WHERE d.id=:deal_id"
        ), {"deal_id": invoice["deal_id"]}).mappings().first()
        if deal is None or int(deal["is_real"] or 0) != 1 or deal["deal_status"] != "awaiting_payment" or deal["order_status"] != "matched":
            return False
        conn.execute(stars_invoices.update().where(
            stars_invoices.c.invoice_id == invoice["invoice_id"]
        ).values(status="pre_checkout_approved", pre_checkout_query_id=query_id,
                 pre_checkout_approved_at=current))
        return True


def settle_telegram_stars_payment(*, payload: str, buyer_user_id: str, currency: str,
                                  amount_stars: int, telegram_payment_charge_id: str,
                                  provider_payment_charge_id: str | None = None,
                                  engine: Engine | None = None,
                                  now: int | None = None) -> dict[str, Any]:
    """Idempotently settle Telegram's successful_payment update and the deal in one DB transaction."""
    db = engine or get_engine()
    init_financial_schema(db)
    current = int(now or time.time())
    charge_id = str(telegram_payment_charge_id).strip()
    if currency != "XTR" or int(amount_stars) <= 0 or not charge_id or len(charge_id) > 256:
        raise ValueError("Invalid Telegram Stars payment receipt")
    statement = select(stars_invoices).where(stars_invoices.c.payload == payload)
    if db.dialect.name == "postgresql":
        statement = statement.with_for_update()
    with db.begin() as conn:
        row = conn.execute(statement).mappings().first()
        if row is None:
            raise ValueError("Unknown Telegram Stars invoice")
        invoice = dict(row)
        if (invoice["buyer_user_id"] != str(buyer_user_id)
                or invoice["currency"] != currency
                or int(invoice["amount_stars"]) != int(amount_stars)):
            raise ValueError("Telegram Stars receipt does not match the invoice")

        prior = conn.execute(select(stars_payments).where(
            (stars_payments.c.telegram_payment_charge_id == charge_id)
            | (stars_payments.c.invoice_id == invoice["invoice_id"])
        )).mappings().first()
        if prior:
            if (prior["invoice_id"] == invoice["invoice_id"]
                    and prior["telegram_payment_charge_id"] == charge_id
                    and prior["buyer_user_id"] == str(buyer_user_id)
                    and int(prior["amount_stars"]) == int(amount_stars)):
                return {"credited": False, "duplicate": True, "invoice_id": invoice["invoice_id"]}
            raise ValueError("Telegram payment charge or invoice was already used")

        if invoice["status"] != "pre_checkout_approved" or invoice["pre_checkout_approved_at"] is None:
            raise ValueError("Telegram payment was not pre-approved")
        payment_event_id = str(uuid.uuid4())
        conn.execute(insert(stars_payments).values(
            payment_event_id=payment_event_id, invoice_id=invoice["invoice_id"],
            telegram_payment_charge_id=charge_id, buyer_user_id=str(buyer_user_id),
            amount_stars=int(amount_stars), currency="XTR", created_at=current,
        ))
        journal = _post(conn,
            idempotency_key="telegram-stars:" + hashlib.sha256(charge_id.encode("utf-8")).hexdigest(),
            source_type="telegram_stars_payment",
            source_reference=invoice["invoice_id"],
            postings=[
                {"account_id": "asset:telegram_stars:clearing", "account_type": "asset",
                 "currency": "XTR", "amount_raw": int(amount_stars)},
                {"account_id": "liability:seller:" + invoice["seller_user_id"] + ":XTR",
                 "account_type": "liability", "currency": "XTR",
                 "amount_raw": -int(amount_stars)},
            ], now=current)
        conn.execute(stars_invoices.update().where(
            stars_invoices.c.invoice_id == invoice["invoice_id"]
        ).values(status="paid", paid_at=current, telegram_payment_charge_id=charge_id,
                 provider_payment_charge_id=provider_payment_charge_id or None))
        changed = conn.execute(text(
            "UPDATE demo_deals SET status='paid' "
            "WHERE id=:deal_id AND is_real=1 AND status IN ('awaiting_payment','payment_pending')"
        ), {"deal_id": invoice["deal_id"]})
        if changed.rowcount != 1:
            current_deal = conn.execute(text(
                "SELECT status FROM demo_deals WHERE id=:deal_id"
            ), {"deal_id": invoice["deal_id"]}).scalar_one_or_none()
            if current_deal != "paid":
                raise RuntimeError("Payment was received but deal state could not be reconciled")
        return {"credited": True, "duplicate": False, "invoice_id": invoice["invoice_id"],
                "payment_event_id": payment_event_id, "entry_id": journal["entry_id"]}


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
