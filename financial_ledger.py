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
    BigInteger, Boolean, CheckConstraint, Column, ForeignKey, Integer, MetaData, String,
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
    Column("asset_kind", String(16), nullable=False, default="jetton"),
    Column("recipient_address", String(128), nullable=False),
    Column("amount_raw", String(80), nullable=False),
    Column("payload", String(256), nullable=False, unique=True),
    Column("expires_at", BigInteger, nullable=False),
    Column("status", String(24), nullable=False),
    Column("created_at", BigInteger, nullable=False))
ton_chain_observations = Table("ton_chain_observations", metadata,
    Column("observation_id", String(36), primary_key=True),
    Column("network", String(32), nullable=False),
    Column("tx_hash", String(128), nullable=False),
    Column("event_index", BigInteger, nullable=False),
    Column("asset_id", String(32), nullable=False),
    Column("provider_id", String(64), nullable=False),
    Column("evidence_hash", String(64), nullable=False),
    Column("finalized", Boolean, nullable=False),
    Column("observed_at", BigInteger, nullable=False),
    UniqueConstraint("network", "tx_hash", "event_index", "asset_id", "provider_id",
                     name="uq_ton_observation_provider_event"))
withdrawal_requests = Table("withdrawal_requests", metadata,
    Column("request_id", String(36), primary_key=True),
    Column("user_id", String(64), nullable=False),
    Column("asset_id", String(32), nullable=False),
    Column("network", String(32), nullable=False),
    Column("destination_address", String(128), nullable=False),
    Column("amount_raw", String(80), nullable=False),
    Column("fee_raw", String(80), nullable=False, default="0"),
    Column("status", String(32), nullable=False),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("request_hash", String(64), nullable=False),
    Column("tx_hash", String(128), unique=True),
    Column("failure_reason", String(500)),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    CheckConstraint("network='ton-mainnet'", name="ck_withdrawal_network"),
    CheckConstraint("CAST(amount_raw AS BIGINT) > 0", name="ck_withdrawal_positive_amount"),
    CheckConstraint("CAST(fee_raw AS BIGINT) >= 0", name="ck_withdrawal_nonnegative_fee"),
    CheckConstraint("status IN ('requested','processing','submitted','confirmed','failed','reconciliation_required','cancelled')",
                    name="ck_withdrawal_status"))
Index("idx_withdrawal_user_status", withdrawal_requests.c.user_id,
      withdrawal_requests.c.status, withdrawal_requests.c.created_at)
withdrawal_events = Table("withdrawal_events", metadata,
    Column("event_id", String(36), primary_key=True),
    Column("request_id", String(36), ForeignKey("withdrawal_requests.request_id"), nullable=False),
    Column("actor_type", String(16), nullable=False),
    Column("actor_id", String(64), nullable=False, default=""),
    Column("event_type", String(32), nullable=False),
    Column("event_data", String(1000), nullable=False, default="{}"),
    Column("created_at", BigInteger, nullable=False),
    CheckConstraint("actor_type IN ('user','system','admin')", name="ck_withdrawal_event_actor"),
    CheckConstraint("event_type IN ('requested','processing','submitted','confirmed','failed','cancelled','reconciliation_required')",
                    name="ck_withdrawal_event_type"))
Index("idx_withdrawal_events_request", withdrawal_events.c.request_id,
      withdrawal_events.c.created_at)
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

stars_refund_requests = Table("telegram_stars_refund_requests", metadata,
    Column("request_id", String(36), primary_key=True),
    Column("invoice_id", String(64), ForeignKey("telegram_stars_invoices.invoice_id"), nullable=False, unique=True),
    Column("buyer_user_id", String(64), nullable=False),
    Column("telegram_payment_charge_id", String(256), nullable=False, unique=True),
    Column("amount_stars", BigInteger, nullable=False),
    Column("reason", String(500), nullable=False, default=""),
    Column("status", String(24), nullable=False, default="requested"),
    Column("requested_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    Column("processed_by", String(64)),
    Column("telegram_response_ref", String(256)),
    Column("reconciliation_note", String(500)),
    CheckConstraint("amount_stars > 0", name="ck_telegram_stars_refund_request_amount"),
    CheckConstraint("status IN ('requested', 'processing', 'succeeded', 'failed', 'reconciliation_required')",
                    name="ck_telegram_stars_refund_request_status"))
Index("idx_telegram_stars_refund_requests_status", stars_refund_requests.c.status, stars_refund_requests.c.requested_at)

telegram_stars_terms_acceptances = Table("telegram_stars_terms_acceptances", metadata,
    Column("acceptance_id", String(36), primary_key=True),
    Column("user_id", String(64), nullable=False),
    Column("terms_version", String(64), nullable=False),
    Column("terms_url", String(2048), nullable=False),
    Column("accepted_at", BigInteger, nullable=False),
    UniqueConstraint("user_id", "terms_version", name="uq_telegram_stars_terms_user_version"),
    CheckConstraint("length(terms_version) BETWEEN 1 AND 64", name="ck_telegram_stars_terms_version"),
    CheckConstraint("terms_url LIKE 'https://%'", name="ck_telegram_stars_terms_https"))

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

def migration_revision_number(version: Any) -> int:
    """Parse the ordered numeric prefix from an Alembic revision identifier."""
    try:
        prefix = str(version or "").split("_", 1)[0]
        return int(prefix) if prefix.isdigit() else 0
    except (TypeError, ValueError):
        return 0


def init_financial_schema(engine: Engine | None = None) -> None:
    db = engine or get_engine()
    if db.dialect.name == "postgresql":
        with db.connect() as conn:
            if not db.dialect.has_table(conn, "alembic_version"):
                raise RuntimeError("PostgreSQL schema must be initialized through Alembic migrations")
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
            if migration_revision_number(version) < 14:
                raise RuntimeError("Financial database migrations through 0014_ownership_proof_index are required")
        return
    metadata.create_all(db)
    if db.dialect.name == "sqlite":
        with db.begin() as conn:
            for table in ("ledger_entries", "ledger_lines", "payment_events", "ton_chain_observations", "withdrawal_events", "telegram_stars_payments", "telegram_stars_refund_events", "telegram_stars_terms_acceptances"):
                for action in ("UPDATE", "DELETE"):
                    conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'append-only financial record'); END"))

def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def ton_trace_evidence_hash(trace: dict[str, Any]) -> str:
    keys = (
        "chain", "asset_kind", "master_address", "source_jetton_wallet",
        "expected_source_jetton_wallet", "sender_address", "recipient_address",
        "amount_raw", "payload", "opcode", "successful", "bounced", "finalized",
        "tx_hash", "event_index",
    )
    return _hash({key: trace.get(key) for key in keys if key in trace})

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
    from asset_policy import get_asset, is_crypto_payment_enabled, validate_payment_amount
    asset = get_asset(asset_id, chain, master_address, "external")
    if not is_crypto_payment_enabled(asset_id):
        raise PermissionError("Crypto payment rail is disabled by compliance policy")
    try:
        amount_raw = validate_payment_amount(asset_id, amount_raw)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if expires_at <= int(time.time()) or not payload:
        raise ValueError("Invalid payment invoice")
    import secrets
    invoice_id = "gri_" + secrets.token_urlsafe(24)
    db = engine or get_engine()
    init_financial_schema(db)
    with db.begin() as conn:
        conn.execute(insert(invoices).values(invoice_id=invoice_id, user_id=str(user_id),
            asset_id=asset.asset_id, chain=asset.chain.value, master_address=asset.master_address,
            asset_kind=asset.asset_kind, recipient_address=recipient_address, amount_raw=str(amount_raw), payload=payload,
            expires_at=expires_at, status="pending", created_at=int(time.time())))
    return invoice_id

def current_telegram_stars_terms_version() -> str:
    version = os.getenv("TELEGRAM_STARS_TERMS_VERSION", "").strip()
    if not version or len(version) > 64:
        raise RuntimeError("A bounded TELEGRAM_STARS_TERMS_VERSION is required")
    return version


def record_telegram_stars_terms_acceptance(*, user_id: str, terms_version: str | None = None,
                                          terms_url: str | None = None,
                                          engine: Engine | None = None,
                                          now: int | None = None) -> dict[str, Any]:
    """Record explicit acceptance of the server-configured current terms version."""
    db = engine or get_engine()
    init_financial_schema(db)
    version = current_telegram_stars_terms_version()
    if terms_version is not None and terms_version != version:
        raise ValueError("Terms version is not current")
    url = (terms_url or os.getenv("TERMS_URL", "")).strip()
    if not url.startswith("https://") or len(url) > 2048:
        raise RuntimeError("A published HTTPS TERMS_URL is required")
    uid = str(user_id).strip()
    if not uid.isdigit() or int(uid) <= 0:
        raise ValueError("Invalid Telegram user ID")
    current = int(now or time.time())
    acceptance_id = str(uuid.uuid4())
    try:
        with db.begin() as conn:
            existing = conn.execute(select(telegram_stars_terms_acceptances).where(
                telegram_stars_terms_acceptances.c.user_id == uid,
                telegram_stars_terms_acceptances.c.terms_version == version,
            )).mappings().first()
            if existing:
                if existing["terms_url"] != url:
                    raise RuntimeError("The current terms URL changed without a version change")
                return {**dict(existing), "duplicate": True}
            conn.execute(insert(telegram_stars_terms_acceptances).values(
                acceptance_id=acceptance_id, user_id=uid, terms_version=version,
                terms_url=url, accepted_at=current,
            ))
            return {"acceptance_id": acceptance_id, "user_id": uid,
                    "terms_version": version, "terms_url": url,
                    "accepted_at": current, "duplicate": False}
    except Exception as exc:
        from sqlalchemy.exc import IntegrityError
        if not isinstance(exc, IntegrityError):
            raise
        with db.connect() as conn:
            existing = conn.execute(select(telegram_stars_terms_acceptances).where(
                telegram_stars_terms_acceptances.c.user_id == uid,
                telegram_stars_terms_acceptances.c.terms_version == version,
            )).mappings().first()
        if existing is None:
            raise
        if existing["terms_url"] != url:
            raise RuntimeError("The current terms URL changed without a version change")
        return {**dict(existing), "duplicate": True}

def has_current_telegram_stars_terms_acceptance(*, user_id: str,
                                                engine: Engine | None = None) -> bool:
    db = engine or get_engine()
    init_financial_schema(db)
    version = current_telegram_stars_terms_version()
    with db.connect() as conn:
        return conn.execute(select(telegram_stars_terms_acceptances.c.acceptance_id).where(
            telegram_stars_terms_acceptances.c.user_id == str(user_id),
            telegram_stars_terms_acceptances.c.terms_version == version,
        )).first() is not None


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
    if not has_current_telegram_stars_terms_acceptance(user_id=str(buyer_user_id), engine=db):
        raise PermissionError("Buyer must explicitly accept the current Telegram Stars terms")

    # Native GIFT RADAR deals use the marketplace ledger, not legacy demo_orders.
    if str(deal_id).startswith("grd_"):
        lock_native = " FOR UPDATE OF d, r, l" if db.dialect.name == "postgresql" else ""
        with db.begin() as conn:
            native = conn.execute(text(
                "SELECT d.deal_id, d.listing_id, d.reservation_id, d.buyer_user_id, d.seller_user_id, "
                "d.price_raw, d.currency, d.status, r.status AS reservation_status, "
                "r.expires_at, l.status AS listing_status "
                "FROM marketplace_deals d "
                "JOIN marketplace_reservations r ON r.reservation_id=d.reservation_id "
                "JOIN marketplace_listings l ON l.listing_id=d.listing_id "
                "WHERE d.deal_id=:deal_id" + lock_native
            ), {"deal_id": str(deal_id)}).mappings().first()
            if native is None:
                raise ValueError("Unknown native marketplace deal")
            if str(buyer_user_id) != str(native["buyer_user_id"]):
                raise PermissionError("Only the native deal buyer can create its payment invoice")
            if native["currency"] != "XTR":
                raise PermissionError("Only XTR-priced native deals can use Telegram Stars")
            if (native["status"] != "reserved" or native["reservation_status"] != "active"
                    or native["listing_status"] != "reserved" or int(native["expires_at"]) <= current):
                raise ValueError("Native deal reservation is no longer payable")
            amount = int(native["price_raw"])
            if amount <= 0 or amount > 2_147_483_647:
                raise ValueError("Telegram Stars amount is outside the supported integer range")
            existing = conn.execute(select(stars_invoices).where(
                stars_invoices.c.deal_id == str(deal_id),
                stars_invoices.c.status.in_(("pending", "pre_checkout_approved")),
            ).order_by(stars_invoices.c.created_at.desc())).mappings().first()
            if existing and int(existing["expires_at"]) > current:
                return {**dict(existing), "title": "GIFT RADAR gift"}
            if existing:
                conn.execute(stars_invoices.update().where(
                    stars_invoices.c.invoice_id == existing["invoice_id"]
                ).values(status="expired"))
            invoice_id = "gri_" + secrets.token_urlsafe(18)
            payload = "grstars:" + secrets.token_urlsafe(24)
            expires_at = current + ttl_seconds
            # Keep the reservation alive through the invoice window and webhook delivery
            # grace period so the expiry worker cannot relist a gift during checkout.
            conn.execute(text(
                "UPDATE marketplace_reservations SET expires_at=CASE "
                "WHEN expires_at < :hold_until THEN :hold_until ELSE expires_at END "
                "WHERE reservation_id=:reservation_id AND status='active'"
            ), {"hold_until": expires_at + 120,
                 "reservation_id": native["reservation_id"]})
            conn.execute(insert(stars_invoices).values(
                invoice_id=invoice_id, payload=payload, deal_id=str(deal_id),
                order_id=str(native["listing_id"]), buyer_user_id=str(native["buyer_user_id"]),
                seller_user_id=str(native["seller_user_id"]), amount_stars=amount,
                currency="XTR", status="pending", expires_at=expires_at, created_at=current,
            ))
            row = conn.execute(select(stars_invoices).where(
                stars_invoices.c.invoice_id == invoice_id)).mappings().one()
            return {**dict(row), "title": "GIFT RADAR gift"}

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
        if str(invoice["deal_id"]).startswith("grd_"):
            native = conn.execute(text(
                "SELECT d.status AS deal_status, r.status AS reservation_status, "
                "r.expires_at, l.status AS listing_status "
                "FROM marketplace_deals d "
                "JOIN marketplace_reservations r ON r.reservation_id=d.reservation_id "
                "JOIN marketplace_listings l ON l.listing_id=d.listing_id "
                "WHERE d.deal_id=:deal_id"
            ), {"deal_id": invoice["deal_id"]}).mappings().first()
            if (native is None or native["deal_status"] != "reserved"
                    or native["reservation_status"] != "active"
                    or native["listing_status"] != "reserved"
                    or int(native["expires_at"]) <= current):
                return False
            # Pre-checkout is the final authorization boundary. Extend the lease
            # beyond the invoice expiry to cover Telegram's successful_payment webhook.
            conn.execute(text(
                "UPDATE marketplace_reservations SET expires_at=CASE "
                "WHEN expires_at < :hold_until THEN :hold_until ELSE expires_at END "
                "WHERE reservation_id=(SELECT reservation_id FROM marketplace_deals WHERE deal_id=:deal_id) "
                "AND status='active'"
            ), {"hold_until": int(invoice["expires_at"]) + 120,
                 "deal_id": invoice["deal_id"]})
        else:
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
        native_deal = None
        if str(invoice["deal_id"]).startswith("grd_"):
            lock_native = " FOR UPDATE OF d, r, l" if db.dialect.name == "postgresql" else ""
            native_deal = conn.execute(text(
                "SELECT d.deal_id, d.status AS deal_status, d.buyer_user_id, d.seller_user_id, "
                "d.price_raw, d.currency, d.reservation_id, d.listing_id, "
                "r.status AS reservation_status, r.expires_at, l.status AS listing_status "
                "FROM marketplace_deals d "
                "JOIN marketplace_reservations r ON r.reservation_id=d.reservation_id "
                "JOIN marketplace_listings l ON l.listing_id=d.listing_id "
                "WHERE d.deal_id=:deal_id" + lock_native
            ), {"deal_id": invoice["deal_id"]}).mappings().first()
            if (native_deal is None or native_deal["deal_status"] != "reserved"
                    or native_deal["reservation_status"] != "active"
                    or native_deal["listing_status"] != "reserved"
                    or int(native_deal["expires_at"]) <= current
                    or str(native_deal["buyer_user_id"]) != str(buyer_user_id)
                    or str(native_deal["seller_user_id"]) != str(invoice["seller_user_id"])
                    or native_deal["currency"] != "XTR"
                    or int(native_deal["price_raw"]) != int(amount_stars)):
                raise RuntimeError("Stars receipt does not match an active native marketplace deal")
        else:
            deal_state = conn.execute(text(
                "SELECT d.is_real, d.status AS deal_status, o.status AS order_status "
                "FROM demo_deals d JOIN demo_orders o ON o.id=d.order_id WHERE d.id=:deal_id"
            ), {"deal_id": invoice["deal_id"]}).mappings().first()
            if (deal_state is None or int(deal_state["is_real"] or 0) != 1
                    or deal_state["deal_status"] not in {"awaiting_payment", "payment_pending"}
                    or deal_state["order_status"] != "matched"):
                raise RuntimeError("Payment receipt does not match a payable real deal")
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
                {"account_id": f"liability:deal_hold:{invoice['deal_id']}:XTR",
                 "account_type": "liability", "currency": "XTR",
                 "amount_raw": -int(amount_stars)},
            ], now=current)
        conn.execute(stars_invoices.update().where(
            stars_invoices.c.invoice_id == invoice["invoice_id"]
        ).values(status="paid", paid_at=current, telegram_payment_charge_id=charge_id,
                 provider_payment_charge_id=provider_payment_charge_id or None))
        if native_deal is not None:
            changed = conn.execute(text(
                "UPDATE marketplace_deals SET status='payment_confirmed', "
                "payment_invoice_id=:invoice_id, payment_event_id=:payment_event_id, updated_at=:now "
                "WHERE deal_id=:deal_id AND status='reserved'"
            ), {"invoice_id": invoice["invoice_id"], "payment_event_id": payment_event_id,
                 "now": current, "deal_id": invoice["deal_id"]})
            if changed.rowcount != 1:
                raise RuntimeError("Stars payment was received but native deal state could not be reconciled")
            conn.execute(text(
                "INSERT INTO marketplace_deal_events "
                "(event_id,deal_id,actor_type,actor_id,event_type,event_data,created_at) "
                "VALUES (:event_id,:deal_id,'system','', 'payment_confirmed',:event_data,:created_at)"
            ), {"event_id": uuid.uuid4().hex, "deal_id": invoice["deal_id"],
                 "event_data": json.dumps({"invoice_id": invoice["invoice_id"],
                                           "payment_event_id": payment_event_id,
                                           "journal_id": journal["entry_id"]},
                                          sort_keys=True, separators=(",", ":")),
                 "created_at": current})
        else:
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



def request_telegram_stars_refund(*, invoice_id: str, buyer_user_id: str, reason: str,
                                  engine: Engine | None = None, now: int | None = None) -> dict[str, Any]:
    """Create an auditable buyer refund request; this never calls Telegram or moves funds."""
    db = engine or get_engine()
    init_financial_schema(db)
    current = int(now or time.time())
    clean_reason = " ".join(str(reason or "").split())[:500]
    lock = " FOR UPDATE" if db.dialect.name == "postgresql" else ""
    with db.begin() as conn:
        invoice = conn.execute(text(
            "SELECT invoice_id, deal_id, buyer_user_id, amount_stars, status, telegram_payment_charge_id "
            "FROM telegram_stars_invoices WHERE invoice_id=:invoice_id" + lock
        ), {"invoice_id": str(invoice_id)}).mappings().first()
        if invoice is None:
            raise ValueError("Unknown Telegram Stars invoice")
        if str(invoice["buyer_user_id"]) != str(buyer_user_id):
            raise PermissionError("Only the original buyer can request a refund")
        if invoice["status"] != "paid" or not invoice["telegram_payment_charge_id"]:
            raise ValueError("Only a paid, non-refunded invoice can be refunded")
        if str(invoice["deal_id"]).startswith("grd_"):
            deal_status = conn.execute(text(
                "SELECT status FROM marketplace_deals WHERE deal_id=:deal_id"
            ), {"deal_id": invoice["deal_id"]}).scalar_one_or_none()
            if deal_status not in {"payment_confirmed", "disputed"}:
                raise PermissionError("Native deal is not eligible for a pre-transfer Stars refund")
        else:
            deal_status = conn.execute(text(
                "SELECT status FROM demo_deals WHERE id=:deal_id"
            ), {"deal_id": invoice["deal_id"]}).scalar_one_or_none()
            if deal_status not in {"paid", "delivery_pending", "disputed"}:
                raise PermissionError("Completed or inactive deals cannot be refunded")
        payment = conn.execute(select(stars_payments).where(
            stars_payments.c.invoice_id == str(invoice_id))).mappings().first()
        if (payment is None or payment["telegram_payment_charge_id"] != invoice["telegram_payment_charge_id"]
                or int(payment["amount_stars"]) != int(invoice["amount_stars"])):
            raise RuntimeError("Payment receipt does not reconcile with the invoice")
        existing = conn.execute(select(stars_refund_requests).where(
            stars_refund_requests.c.invoice_id == str(invoice_id)).with_for_update()
            if db.dialect.name == "postgresql" else
            select(stars_refund_requests).where(stars_refund_requests.c.invoice_id == str(invoice_id))
        ).mappings().first()
        if existing:
            status = existing["status"]
            if status != "failed":
                return {"request_id": existing["request_id"], "status": status, "duplicate": True}
            request_id = existing["request_id"]
            conn.execute(stars_refund_requests.update().where(
                stars_refund_requests.c.request_id == request_id
            ).values(status="requested", reason=clean_reason, updated_at=current, processed_by=None,
                     telegram_response_ref=None))
        else:
            request_id = str(uuid.uuid4())
            conn.execute(insert(stars_refund_requests).values(
                request_id=request_id, invoice_id=str(invoice_id), buyer_user_id=str(buyer_user_id),
                telegram_payment_charge_id=invoice["telegram_payment_charge_id"],
                amount_stars=int(invoice["amount_stars"]), reason=clean_reason,
                status="requested", requested_at=current, updated_at=current,
            ))
        conn.execute(insert(stars_refund_events).values(
            refund_event_id=str(uuid.uuid4()), invoice_id=str(invoice_id),
            telegram_payment_charge_id=invoice["telegram_payment_charge_id"],
            event_type="refund_requested", reason=clean_reason, created_at=current,
        ))
        return {"request_id": request_id, "status": "requested", "duplicate": False}


def prepare_telegram_stars_refund(*, request_id: str, admin_user_id: str,
                                  engine: Engine | None = None, now: int | None = None) -> dict[str, Any]:
    """Claim a refund once before calling Telegram; processing claims cannot be blindly retried."""
    db = engine or get_engine()
    init_financial_schema(db)
    current = int(now or time.time())
    statement = select(stars_refund_requests).where(
        stars_refund_requests.c.request_id == str(request_id))
    if db.dialect.name == "postgresql":
        statement = statement.with_for_update()
    with db.begin() as conn:
        row = conn.execute(statement).mappings().first()
        if row is None:
            raise ValueError("Unknown refund request")
        if row["status"] != "requested":
            raise ValueError("Refund request is not ready for execution")
        invoice = conn.execute(select(stars_invoices).where(
            stars_invoices.c.invoice_id == row["invoice_id"])).mappings().first()
        if invoice is None or invoice["status"] != "paid":
            raise ValueError("Invoice is not in a refundable paid state")
        if str(invoice["deal_id"]).startswith("grd_"):
            native_status = conn.execute(text(
                "SELECT status FROM marketplace_deals WHERE deal_id=:deal_id"
            ), {"deal_id": invoice["deal_id"]}).scalar_one_or_none()
            if native_status not in {"payment_confirmed", "disputed"}:
                raise PermissionError("Native deal transfer state blocks a Stars refund")
        else:
            legacy_status = conn.execute(text(
                "SELECT status FROM demo_deals WHERE id=:deal_id"
            ), {"deal_id": invoice["deal_id"]}).scalar_one_or_none()
            if legacy_status not in {"paid", "delivery_pending", "disputed"}:
                raise PermissionError("Deal state blocks a Stars refund")
        conn.execute(stars_refund_requests.update().where(
            stars_refund_requests.c.request_id == str(request_id)
        ).values(status="processing", processed_by=str(admin_user_id), updated_at=current))
        return {
            "request_id": str(request_id), "invoice_id": row["invoice_id"],
            "buyer_user_id": row["buyer_user_id"],
            "telegram_payment_charge_id": row["telegram_payment_charge_id"],
            "amount_stars": int(row["amount_stars"]),
        }


def complete_telegram_stars_refund(*, request_id: str, telegram_response_ref: str = "",
                                   engine: Engine | None = None, now: int | None = None) -> dict[str, Any]:
    """Record Telegram-confirmed refund and post the compensating journal entry atomically."""
    db = engine or get_engine()
    init_financial_schema(db)
    current = int(now or time.time())
    statement = select(stars_refund_requests).where(
        stars_refund_requests.c.request_id == str(request_id))
    if db.dialect.name == "postgresql":
        statement = statement.with_for_update()
    with db.begin() as conn:
        row = conn.execute(statement).mappings().first()
        if row is None:
            raise ValueError("Unknown refund request")
        if row["status"] == "succeeded":
            return {"refunded": False, "duplicate": True, "request_id": str(request_id)}
        if row["status"] != "processing":
            raise ValueError("Refund was not claimed for execution")
        invoice = conn.execute(select(stars_invoices).where(
            stars_invoices.c.invoice_id == row["invoice_id"])).mappings().first()
        if (invoice is None or invoice["status"] != "paid"
                or invoice["telegram_payment_charge_id"] != row["telegram_payment_charge_id"]):
            raise RuntimeError("Refund request no longer matches the paid invoice")
        native_deal = str(invoice["deal_id"]).startswith("grd_")
        if native_deal:
            deal_status = conn.execute(text(
                "SELECT status FROM marketplace_deals WHERE deal_id=:deal_id"
            ), {"deal_id": invoice["deal_id"]}).scalar_one_or_none()
            if deal_status not in {"payment_confirmed", "disputed"}:
                raise PermissionError("Native deal is not eligible for a pre-transfer Stars refund")
        else:
            deal_status = conn.execute(text(
                "SELECT status FROM demo_deals WHERE id=:deal_id"
            ), {"deal_id": invoice["deal_id"]}).scalar_one_or_none()
            if deal_status not in {"paid", "delivery_pending", "disputed"}:
                raise PermissionError("Completed or inactive deals cannot be refunded through the Stars hold")
        amount = int(row["amount_stars"])
        hold_account = "liability:deal_hold:" + str(invoice["deal_id"]) + ":XTR"
        held_amount = -_account_balance_in_connection(conn, hold_account, "XTR")
        if held_amount != amount:
            raise PermissionError("Stars proceeds are not fully available in the original deal hold")
        journal = _post(conn,
            idempotency_key="telegram-stars-refund:" + hashlib.sha256(
                row["telegram_payment_charge_id"].encode("utf-8")).hexdigest(),
            source_type="telegram_stars_refund", source_reference=row["invoice_id"],
            postings=[
                {"account_id": "asset:telegram_stars:clearing", "account_type": "asset",
                 "currency": "XTR", "amount_raw": -amount},
                {"account_id": f"liability:deal_hold:{invoice['deal_id']}:XTR",
                 "account_type": "liability", "currency": "XTR", "amount_raw": amount},
            ], now=current)
        conn.execute(stars_invoices.update().where(
            stars_invoices.c.invoice_id == row["invoice_id"]
        ).values(status="refunded", refunded_at=current))
        if native_deal:
            conn.execute(text(
                "UPDATE marketplace_deals SET status='refunded', updated_at=:now "
                "WHERE deal_id=:deal_id AND status IN ('payment_confirmed','disputed')"
            ), {"deal_id": invoice["deal_id"], "now": current})
            conn.execute(text(
                "UPDATE marketplace_reservations SET status='expired' "
                "WHERE reservation_id=(SELECT reservation_id FROM marketplace_deals WHERE deal_id=:deal_id) "
                "AND status='active'"
            ), {"deal_id": invoice["deal_id"]})
            conn.execute(text(
                "UPDATE marketplace_listings SET status='pending_verification', updated_at=:now "
                "WHERE listing_id=(SELECT listing_id FROM marketplace_deals WHERE deal_id=:deal_id) "
                "AND status='reserved'"
            ), {"deal_id": invoice["deal_id"], "now": current})
            conn.execute(text(
                "INSERT INTO marketplace_deal_events "
                "(event_id,deal_id,actor_type,actor_id,event_type,event_data,created_at) "
                "VALUES (:event_id,:deal_id,'system','', 'deal_refunded',:event_data,:created_at)"
            ), {"event_id": uuid.uuid4().hex, "deal_id": invoice["deal_id"],
                 "event_data": json.dumps({"reason": "telegram_stars_refund_confirmed",
                                           "listing_status": "pending_verification"},
                                          sort_keys=True, separators=(",", ":")),
                 "created_at": current})
        else:
            conn.execute(text(
                "UPDATE demo_deals SET status='cancelled' WHERE id=:deal_id "
                "AND status IN ('paid','delivery_pending','disputed')"
            ), {"deal_id": invoice["deal_id"]})
        conn.execute(stars_refund_requests.update().where(
            stars_refund_requests.c.request_id == str(request_id)
        ).values(status="succeeded", updated_at=current,
                 telegram_response_ref=str(telegram_response_ref)[:256] or None))
        conn.execute(insert(stars_refund_events).values(
            refund_event_id=str(uuid.uuid4()), invoice_id=row["invoice_id"],
            telegram_payment_charge_id=row["telegram_payment_charge_id"],
            event_type="refund_succeeded", reason=row["reason"], created_at=current,
        ))
        return {"refunded": True, "duplicate": False, "request_id": str(request_id),
                "entry_id": journal["entry_id"]}


def require_telegram_stars_refund_reconciliation(*, request_id: str, reason: str,
                                                engine: Engine | None = None,
                                                now: int | None = None) -> None:
    """Quarantine an ambiguous Telegram refund result; never make it retryable automatically."""
    db = engine or get_engine()
    init_financial_schema(db)
    current = int(now or time.time())
    clean_reason = " ".join(str(reason or "").split())[:500] or "Telegram refund outcome is ambiguous"
    statement = select(stars_refund_requests).where(
        stars_refund_requests.c.request_id == str(request_id))
    if db.dialect.name == "postgresql":
        statement = statement.with_for_update()
    with db.begin() as conn:
        row = conn.execute(statement).mappings().first()
        if row is None:
            raise ValueError("Unknown refund request")
        if row["status"] == "reconciliation_required":
            return
        if row["status"] != "processing":
            raise ValueError("Only an ambiguous processing refund can require reconciliation")
        conn.execute(stars_refund_requests.update().where(
            stars_refund_requests.c.request_id == str(request_id)
        ).values(status="reconciliation_required", reconciliation_note=clean_reason,
                 updated_at=current))

def fail_telegram_stars_refund(*, request_id: str, reason: str,
                               engine: Engine | None = None, now: int | None = None) -> None:
    """Record a definite Telegram rejection. Network timeouts must remain processing for reconciliation."""
    db = engine or get_engine()
    init_financial_schema(db)
    current = int(now or time.time())
    clean_reason = " ".join(str(reason or "").split())[:500] or "Telegram rejected refund"
    with db.begin() as conn:
        row = conn.execute(select(stars_refund_requests).where(
            stars_refund_requests.c.request_id == str(request_id)
        )).mappings().first()
        if row is None or row["status"] != "processing":
            raise ValueError("Refund request is not processing")
        conn.execute(stars_refund_requests.update().where(
            stars_refund_requests.c.request_id == str(request_id)
        ).values(status="failed", updated_at=current))
        conn.execute(insert(stars_refund_events).values(
            refund_event_id=str(uuid.uuid4()), invoice_id=row["invoice_id"],
            telegram_payment_charge_id=row["telegram_payment_charge_id"],
            event_type="refund_failed", reason=clean_reason, created_at=current,
        ))

def verify_ton_payment_trace(trace: dict[str, Any], invoice: dict[str, Any]) -> None:
    """Validate normalized evidence from trusted TON providers, never client-supplied claims."""
    common = ("chain", "recipient_address", "amount_raw", "payload", "successful",
              "bounced", "finalized", "tx_hash", "event_index")
    if any(key not in trace for key in common):
        raise ValueError("Incomplete on-chain evidence")
    if trace["chain"] != "ton-mainnet" or trace["chain"] != invoice["chain"]:
        raise ValueError("Wrong blockchain network")
    if trace["recipient_address"] != invoice["recipient_address"]:
        raise ValueError("Payment recipient mismatch")
    if int(trace["amount_raw"]) != int(invoice["amount_raw"]):
        raise ValueError("Payment amount mismatch")
    if trace["payload"] != invoice["payload"]:
        raise ValueError("Invoice payload mismatch")
    if trace["successful"] is not True or trace["bounced"] is not False or trace["finalized"] is not True:
        raise ValueError("Transaction unsuccessful, bounced, or not finalized")
    if not trace["tx_hash"] or int(trace["event_index"]) < 0:
        raise ValueError("Invalid chain event identity")

    asset_kind = invoice.get("asset_kind", "jetton")
    if asset_kind == "native":
        if invoice["asset_id"] != "GRAM" or invoice["master_address"] not in ("", "native"):
            raise ValueError("Native GRAM invoice has an invalid asset identity")
        if trace.get("asset_kind") != "native" or not trace.get("sender_address"):
            raise ValueError("Native GRAM transfer evidence is incomplete")
        if trace.get("master_address") or trace.get("opcode") is not None:
            raise ValueError("Native GRAM must not be represented as a Jetton transfer")
        return

    jetton_required = ("master_address", "source_jetton_wallet",
                       "expected_source_jetton_wallet", "opcode")
    if any(key not in trace for key in jetton_required):
        raise ValueError("Incomplete Jetton transfer evidence")
    if trace["master_address"] != invoice["master_address"]:
        raise ValueError("Token master mismatch")
    if trace["source_jetton_wallet"] != trace["expected_source_jetton_wallet"]:
        raise ValueError("Sender is not the canonical Jetton wallet")
    if str(trace["opcode"]).lower() not in {"0x7362d09c", "1935855772"}:
        raise ValueError("Unexpected Jetton transfer notification opcode")


def _record_withdrawal_event(conn: Connection, *, request_id: str, actor_type: str,
                             actor_id: str, event_type: str, data: dict[str, Any],
                             now: int) -> None:
    conn.execute(insert(withdrawal_events).values(
        event_id=str(uuid.uuid4()), request_id=request_id, actor_type=actor_type,
        actor_id=str(actor_id or "")[:64], event_type=event_type,
        event_data=json.dumps(data, sort_keys=True, separators=(",", ":"))[:1000],
        created_at=now,
    ))


def _account_balance_in_connection(conn: Connection, account_id: str, currency: str) -> int:
    values = conn.execute(select(lines.c.amount_raw).where(
        lines.c.account_id == account_id, lines.c.currency == currency.upper()
    )).scalars()
    return sum(int(value) for value in values)


def settle_verified_ton_payment(*, invoice_id: str, trace: dict[str, Any],
                                engine: Engine | None = None,
                                now: int | None = None) -> dict[str, Any]:
    """Atomically verify, deduplicate and credit one finalized TON native/Jetton transfer."""
    db = engine or get_engine()
    init_financial_schema(db)
    current = int(now or time.time())
    statement = select(invoices).where(invoices.c.invoice_id == invoice_id)
    if db.dialect.name == "postgresql":
        statement = statement.with_for_update()
    with db.begin() as conn:
        row = conn.execute(statement).mappings().first()
        if row is None:
            raise ValueError("Unknown payment invoice")
        invoice = dict(row)
        verify_ton_payment_trace(trace, invoice)
        evidence_hash = ton_trace_evidence_hash(trace)
        observations = conn.execute(select(
            ton_chain_observations.c.provider_id,
            ton_chain_observations.c.evidence_hash,
            ton_chain_observations.c.finalized,
        ).where(
            ton_chain_observations.c.network == trace["chain"],
            ton_chain_observations.c.tx_hash == trace["tx_hash"],
            ton_chain_observations.c.event_index == int(trace["event_index"]),
            ton_chain_observations.c.asset_id == invoice["asset_id"],
        )).mappings().all()
        independent_providers = {
            item["provider_id"] for item in observations
            if bool(item["finalized"]) and item["evidence_hash"] == evidence_hash
        }
        if len(independent_providers) < 2:
            raise PermissionError("Two matching finalized TON provider observations are required")
        duplicate = conn.execute(select(payment_events.c.payment_event_id).where(
            payment_events.c.chain == trace["chain"], payment_events.c.tx_hash == trace["tx_hash"],
            payment_events.c.event_index == int(trace["event_index"]),
            payment_events.c.asset_id == invoice["asset_id"])).first()
        if duplicate:
            return {"credited": False, "duplicate": True}
        if invoice["status"] != "pending" or int(invoice["expires_at"]) <= current:
            raise ValueError("Invoice is not payable")
        event_id = str(uuid.uuid4())
        conn.execute(insert(payment_events).values(payment_event_id=event_id, invoice_id=invoice_id,
            chain=trace["chain"], tx_hash=trace["tx_hash"], event_index=int(trace["event_index"]),
            asset_id=invoice["asset_id"], amount_raw=str(int(trace["amount_raw"])),
            recipient_address=trace["recipient_address"], created_at=current))
        asset_id = invoice["asset_id"]
        result = _post(conn,
            idempotency_key=f"chain:{trace['chain']}:{trace['tx_hash']}:{trace['event_index']}:{asset_id}",
            source_type="verified_chain_payment", source_reference=invoice_id,
            postings=[
                {"account_id": f"asset:clearing:{asset_id}", "account_type": "asset",
                 "currency": asset_id, "amount_raw": int(trace["amount_raw"])},
                {"account_id": f"liability:user:{invoice['user_id']}:{asset_id}",
                 "account_type": "liability", "currency": asset_id,
                 "amount_raw": -int(trace["amount_raw"])},
            ], now=current)
        conn.execute(invoices.update().where(invoices.c.invoice_id == invoice_id).values(status="paid"))
        return {"credited": True, "duplicate": False, "entry_id": result["entry_id"], "event_id": event_id}


def request_ton_withdrawal(*, user_id: str, asset_id: str, destination_address: str,
                           amount_raw: int, idempotency_key: str, fee_raw: int = 0,
                           engine: Engine | None = None, now: int | None = None) -> dict[str, Any]:
    """Reserve a user's available TON-denominated liability once; this never signs or broadcasts."""
    from asset_policy import ASSETS
    db = engine or get_engine()
    init_financial_schema(db)
    uid = str(user_id).strip()
    asset = ASSETS.get(asset_id)
    from ton_nft_providers import canonical_ton_address
    destination_input = str(destination_address or "").strip()
    try:
        destination = canonical_ton_address(destination_input)
    except (TypeError, ValueError):
        raise ValueError("A valid TON mainnet destination address is required") from None
    if not destination.startswith("0:"):
        raise ValueError("Only TON basechain destination addresses are supported")
    if not uid.isdigit() or int(uid) <= 0 or asset is None or asset.chain.value != "ton-mainnet":
        raise ValueError("Unsupported withdrawal owner or asset")
    if int(amount_raw) <= 0 or int(fee_raw) < 0 or not idempotency_key or len(idempotency_key) > 200:
        raise ValueError("Invalid withdrawal amount, fee or idempotency key")
    total = int(amount_raw) + int(fee_raw)
    request_hash = _hash({"user_id": uid, "asset_id": asset_id, "destination": destination,
                          "amount_raw": total, "fee_raw": int(fee_raw)})
    current = int(now or time.time())
    statement = select(withdrawal_requests).where(
        withdrawal_requests.c.idempotency_key == idempotency_key)
    if db.dialect.name == "postgresql":
        statement = statement.with_for_update()
    with db.begin() as conn:
        existing = conn.execute(statement).mappings().first()
        if existing:
            if existing["request_hash"] != request_hash:
                raise ValueError("Withdrawal idempotency key was reused with different details")
            return {**dict(existing), "duplicate": True}
        user_account = f"liability:user:{uid}:{asset_id}"
        available = -_account_balance_in_connection(conn, user_account, asset_id)
        if available < total:
            raise ValueError("Insufficient available balance")
        request_id = str(uuid.uuid4())
        conn.execute(insert(withdrawal_requests).values(
            request_id=request_id, user_id=uid, asset_id=asset_id, network="ton-mainnet",
            destination_address=destination, amount_raw=str(int(amount_raw)),
            fee_raw=str(int(fee_raw)), status="requested", idempotency_key=idempotency_key,
            request_hash=request_hash, created_at=current, updated_at=current,
        ))
        _post(conn, idempotency_key="withdrawal-reserve:" + request_id,
            source_type="withdrawal_reserve", source_reference=request_id,
            postings=[
                {"account_id": user_account, "account_type": "liability",
                 "currency": asset_id, "amount_raw": total},
                {"account_id": f"liability:withdrawal_hold:{request_id}:{asset_id}",
                 "account_type": "liability", "currency": asset_id, "amount_raw": -total},
            ], now=current)
        _record_withdrawal_event(conn, request_id=request_id, actor_type="user",
            actor_id=uid, event_type="requested",
            data={"asset_id": asset_id, "amount_raw": int(amount_raw), "fee_raw": int(fee_raw)},
            now=current)
        row = conn.execute(select(withdrawal_requests).where(
            withdrawal_requests.c.request_id == request_id)).mappings().one()
        return {**dict(row), "duplicate": False}


def advance_withdrawal_to_processing(*, request_id: str, engine: Engine | None = None,
                                     now: int | None = None) -> None:
    db = engine or get_engine()
    statement = select(withdrawal_requests).where(withdrawal_requests.c.request_id == request_id)
    if db.dialect.name == "postgresql":
        statement = statement.with_for_update()
    with db.begin() as conn:
        row = conn.execute(statement).mappings().first()
        if row is None or row["status"] != "requested":
            raise ValueError("Withdrawal is not available for processing")
        current = int(now or time.time())
        conn.execute(withdrawal_requests.update().where(
            withdrawal_requests.c.request_id == request_id,
            withdrawal_requests.c.status == "requested"
        ).values(status="processing", updated_at=current))
        _record_withdrawal_event(conn, request_id=request_id, actor_type="system",
            actor_id="", event_type="processing", data={}, now=current)


def mark_withdrawal_submitted(*, request_id: str, tx_hash: str,
                              engine: Engine | None = None, now: int | None = None) -> None:
    db = engine or get_engine()
    tx = str(tx_hash or "").strip()
    if not tx or len(tx) > 128:
        raise ValueError("A confirmed broadcast transaction hash is required")
    with db.begin() as conn:
        changed = conn.execute(withdrawal_requests.update().where(
            withdrawal_requests.c.request_id == request_id,
            withdrawal_requests.c.status == "processing"
        ).values(status="submitted", tx_hash=tx, updated_at=int(now or time.time())))
        if changed.rowcount != 1:
            raise ValueError("Only a processing withdrawal can be submitted")
        _record_withdrawal_event(conn, request_id=request_id, actor_type="system",
            actor_id="", event_type="submitted", data={"tx_hash": tx}, now=int(now or time.time()))


def confirm_ton_withdrawal(*, request_id: str, tx_hash: str,
                           engine: Engine | None = None, now: int | None = None) -> dict[str, Any]:
    db = engine or get_engine()
    current = int(now or time.time())
    statement = select(withdrawal_requests).where(withdrawal_requests.c.request_id == request_id)
    if db.dialect.name == "postgresql":
        statement = statement.with_for_update()
    with db.begin() as conn:
        row = conn.execute(statement).mappings().first()
        if row is None:
            raise ValueError("Unknown withdrawal")
        if row["status"] == "confirmed":
            if row["tx_hash"] != tx_hash:
                raise ValueError("Confirmed withdrawal hash mismatch")
            return {"confirmed": False, "duplicate": True}
        if row["status"] != "submitted" or row["tx_hash"] != tx_hash:
            raise ValueError("Withdrawal is not submitted with this transaction hash")
        total = int(row["amount_raw"]) + int(row["fee_raw"])
        asset_id = row["asset_id"]
        journal = _post(conn, idempotency_key="withdrawal-settle:" + request_id,
            source_type="withdrawal_settlement", source_reference=request_id,
            postings=[
                {"account_id": f"liability:withdrawal_hold:{request_id}:{asset_id}",
                 "account_type": "liability", "currency": asset_id, "amount_raw": total},
                {"account_id": f"asset:clearing:{asset_id}", "account_type": "asset",
                 "currency": asset_id, "amount_raw": -total},
            ], now=current)
        conn.execute(withdrawal_requests.update().where(
            withdrawal_requests.c.request_id == request_id
        ).values(status="confirmed", updated_at=current))
        _record_withdrawal_event(conn, request_id=request_id, actor_type="system",
            actor_id="", event_type="confirmed", data={"tx_hash": tx_hash}, now=current)
        return {"confirmed": True, "duplicate": False, "entry_id": journal["entry_id"]}


def quarantine_ton_withdrawal(*, request_id: str, reason: str,
                              engine: Engine | None = None, now: int | None = None) -> None:
    db = engine or get_engine()
    clean = " ".join(str(reason or "").split())[:500] or "Withdrawal outcome is ambiguous"
    with db.begin() as conn:
        changed = conn.execute(withdrawal_requests.update().where(
            withdrawal_requests.c.request_id == request_id,
            withdrawal_requests.c.status.in_(("processing", "submitted"))
        ).values(status="reconciliation_required", failure_reason=clean,
                 updated_at=int(now or time.time())))
        if changed.rowcount != 1:
            raise ValueError("Only an in-flight withdrawal can be quarantined")
        _record_withdrawal_event(conn, request_id=request_id, actor_type="system",
            actor_id="", event_type="reconciliation_required", data={"reason": clean},
            now=int(now or time.time()))


def release_ton_withdrawal(*, request_id: str, reason: str,
                           definitive_not_broadcast: bool = False,
                           engine: Engine | None = None, now: int | None = None) -> dict[str, Any]:
    """Release a hold only when cancellation or non-broadcast failure is definitive."""
    if definitive_not_broadcast is not True:
        raise PermissionError("Funds can only be released after definitive proof that no transfer was broadcast")
    db = engine or get_engine()
    current = int(now or time.time())
    statement = select(withdrawal_requests).where(withdrawal_requests.c.request_id == request_id)
    if db.dialect.name == "postgresql":
        statement = statement.with_for_update()
    with db.begin() as conn:
        row = conn.execute(statement).mappings().first()
        if row is None:
            raise ValueError("Unknown withdrawal")
        if row["status"] in ("failed", "cancelled"):
            return {"released": False, "duplicate": True}
        if row["status"] not in ("requested", "processing"):
            raise ValueError("Submitted or ambiguous withdrawals cannot release funds")
        total = int(row["amount_raw"]) + int(row["fee_raw"])
        asset_id = row["asset_id"]
        journal = _post(conn, idempotency_key="withdrawal-release:" + request_id,
            source_type="withdrawal_release", source_reference=request_id,
            postings=[
                {"account_id": f"liability:withdrawal_hold:{request_id}:{asset_id}",
                 "account_type": "liability", "currency": asset_id, "amount_raw": total},
                {"account_id": f"liability:user:{row['user_id']}:{asset_id}",
                 "account_type": "liability", "currency": asset_id, "amount_raw": -total},
            ], now=current)
        status = "cancelled" if row["status"] == "requested" else "failed"
        clean_reason = " ".join(str(reason or "").split())[:500]
        conn.execute(withdrawal_requests.update().where(
            withdrawal_requests.c.request_id == request_id
        ).values(status=status, failure_reason=clean_reason, updated_at=current))
        _record_withdrawal_event(conn, request_id=request_id, actor_type="system",
            actor_id="", event_type=status, data={"reason": clean_reason}, now=current)
        return {"released": True, "duplicate": False, "entry_id": journal["entry_id"]}
