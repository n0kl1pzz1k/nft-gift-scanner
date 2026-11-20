"""HASSLE NFT marketplace offers, deal lifecycle, payments and asset delivery.

Reservations are unpaid until a provider confirms settlement. TON and Telegram
Stars checkout are feature-gated; escrow accounting, disputes, refunds, timeouts
and TON NFT ownership verification are recorded independently of wallet keys.
"""
from __future__ import annotations

import os
import json
import sqlite3
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Callable, Literal

from fastapi import APIRouter, Header, HTTPException, Query

from marketplace import _enforce_user_rate_limit, _verified_asset_actions_enabled, _test_unverified_assets_allowed, _migrate_legacy_marketplace_table_names
from database import begin_write, select_for_update, require_sqlite_connection, db as _database_db
from pydantic import BaseModel, Field, field_validator
from marketplace_payments import list_escrow_events

DB_PATH = os.getenv("HASSLE_NFT_DB_PATH", os.getenv("GIFT_RADAR_DB_PATH", os.getenv("DATABASE_PATH", "./data/gift-radar.sqlite3")))


def _db():
    # Preserve the legacy path override while centralizing connection lifetime.
    return _database_db(DB_PATH)


def init_marketplace_offers_db() -> None:
    with _db() as conn:
        if bool(getattr(conn, "supports_for_update", False)):
            from marketplace_offers_postgres import initialize_postgres_offer_schema

            initialize_postgres_offer_schema(conn)
            return
        require_sqlite_connection(conn, "Marketplace offers schema initialization")
        _migrate_legacy_marketplace_table_names(conn)
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS marketplace_offers (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            sender_user_id TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            amount TEXT NOT NULL,
            currency TEXT NOT NULL CHECK(currency IN ('TON', 'XTR')),
            note TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK(status IN ('pending', 'accepted', 'declined', 'withdrawn')),
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            FOREIGN KEY(order_id) REFERENCES marketplace_orders(id)
        );
        CREATE INDEX IF NOT EXISTS idx_marketplace_offers_order ON marketplace_offers(order_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_marketplace_offers_sender ON marketplace_offers(sender_user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_marketplace_offers_status ON marketplace_offers(status, created_at DESC);
        CREATE TABLE IF NOT EXISTS marketplace_deals (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            offer_id TEXT NOT NULL UNIQUE,
            listing_owner_id TEXT NOT NULL,
            offer_sender_id TEXT NOT NULL,
            agreed_amount TEXT NOT NULL,
            currency TEXT NOT NULL CHECK(currency IN ('TON', 'XTR')),
            created_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'payment_not_enabled'
                CHECK(status IN ('payment_not_enabled', 'awaiting_payment', 'payment_pending', 'paid', 'delivery_pending', 'completed', 'disputed', 'cancelled', 'expired')),
            FOREIGN KEY(order_id) REFERENCES marketplace_orders(id),
            FOREIGN KEY(offer_id) REFERENCES marketplace_offers(id)
        );
        CREATE TABLE IF NOT EXISTS marketplace_deal_events (
            id TEXT PRIMARY KEY,
            deal_id TEXT NOT NULL,
            order_id TEXT NOT NULL,
            actor_user_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN ('deal_created', 'listing_reserved', 'payment_not_enabled')),
            event_data TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL,
            FOREIGN KEY(deal_id) REFERENCES marketplace_deals(id),
            FOREIGN KEY(order_id) REFERENCES marketplace_orders(id)
        );
        CREATE INDEX IF NOT EXISTS idx_marketplace_deal_events_deal_created
            ON marketplace_deal_events(deal_id, created_at ASC, id ASC);
        CREATE TRIGGER IF NOT EXISTS trg_marketplace_deal_events_no_update
        BEFORE UPDATE ON marketplace_deal_events
        BEGIN
            SELECT RAISE(ABORT, 'deal events are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_marketplace_deal_events_no_delete
        BEFORE DELETE ON marketplace_deal_events
        BEGIN
            SELECT RAISE(ABORT, 'deal events are immutable');
        END;
        CREATE TABLE IF NOT EXISTS marketplace_deal_lifecycle_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            deal_id TEXT NOT NULL,
            order_id TEXT NOT NULL,
            actor_user_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN ('deal_cancelled')),
            event_data TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL,
            FOREIGN KEY(deal_id) REFERENCES marketplace_deals(id),
            FOREIGN KEY(order_id) REFERENCES marketplace_orders(id)
        );
        CREATE INDEX IF NOT EXISTS idx_marketplace_deal_lifecycle_events_deal
            ON marketplace_deal_lifecycle_events(deal_id, sequence ASC);
        CREATE TRIGGER IF NOT EXISTS trg_marketplace_deal_lifecycle_events_no_update
        BEFORE UPDATE ON marketplace_deal_lifecycle_events
        BEGIN
            SELECT RAISE(ABORT, 'deal lifecycle events are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_marketplace_deal_lifecycle_events_no_delete
        BEFORE DELETE ON marketplace_deal_lifecycle_events
        BEGIN
            SELECT RAISE(ABORT, 'deal lifecycle events are immutable');
        END;
        CREATE TABLE IF NOT EXISTS marketplace_notifications (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN ('offer_received', 'offer_accepted', 'offer_declined', 'offer_withdrawn')),
            offer_id TEXT NOT NULL,
            order_id TEXT NOT NULL,
            order_title TEXT NOT NULL DEFAULT '',
            actor_name TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            read_at INTEGER,
            FOREIGN KEY(offer_id) REFERENCES marketplace_offers(id),
            FOREIGN KEY(order_id) REFERENCES marketplace_orders(id)
        );
        CREATE INDEX IF NOT EXISTS idx_marketplace_notifications_user_created
            ON marketplace_notifications(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_marketplace_notifications_user_unread
            ON marketplace_notifications(user_id, read_at, created_at DESC);
        CREATE TRIGGER IF NOT EXISTS trg_marketplace_offer_received
        AFTER INSERT ON marketplace_offers
        BEGIN
            INSERT INTO marketplace_notifications
                (id, user_id, event_type, offer_id, order_id, order_title, actor_name, created_at)
            SELECT 'nt-' || lower(hex(randomblob(8))), o.user_id, 'offer_received',
                   NEW.id, NEW.order_id, o.title, NEW.sender_name, NEW.created_at
            FROM marketplace_orders o
            WHERE o.id = NEW.order_id AND o.user_id <> NEW.sender_user_id;
        END;
        CREATE TRIGGER IF NOT EXISTS trg_marketplace_offer_status_notification
        AFTER UPDATE OF status ON marketplace_offers
        WHEN OLD.status = 'pending' AND NEW.status <> OLD.status
        BEGIN
            INSERT INTO marketplace_notifications
                (id, user_id, event_type, offer_id, order_id, order_title, actor_name, created_at)
            SELECT 'nt-' || lower(hex(randomblob(8))),
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
              AND NEW.status IN ('accepted', 'declined', 'withdrawn')
              AND (NEW.status <> 'withdrawn' OR o.user_id <> NEW.sender_user_id);
        END;
        CREATE TRIGGER IF NOT EXISTS trg_marketplace_order_cancel_declines_offers
        AFTER UPDATE OF status ON marketplace_orders
        WHEN OLD.status = 'active' AND NEW.status = 'cancelled'
        BEGIN
            UPDATE marketplace_offers SET status='declined', updated_at=NEW.updated_at
            WHERE order_id=NEW.id AND status='pending';
        END;
        """)
        # Older releases enforced one deal per order forever. Rebuild that table
        # so cancelled/expired history remains intact while a fresh reservation can be made.
        unique_order_index = False
        for index in conn.execute("PRAGMA index_list(marketplace_deals)").fetchall():
            if index["unique"]:
                columns = [row["name"] for row in conn.execute(
                    f"PRAGMA index_info('{index['name']}')"
                ).fetchall()]
                if columns == ["order_id"]:
                    unique_order_index = True
                    break
        if unique_order_index:
            conn.execute("PRAGMA foreign_keys = OFF")
            try:
                begin_write(conn, "schema:marketplace-deals-reservation-index")
                conn.execute("""
                    CREATE TABLE marketplace_deals_new (
                        id TEXT PRIMARY KEY,
                        order_id TEXT NOT NULL,
                        offer_id TEXT NOT NULL UNIQUE,
                        listing_owner_id TEXT NOT NULL,
                        offer_sender_id TEXT NOT NULL,
                        agreed_amount TEXT NOT NULL,
                        currency TEXT NOT NULL CHECK(currency IN ('TON', 'XTR')),
                        created_at INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'payment_not_enabled'
                            CHECK(status IN ('payment_not_enabled', 'awaiting_payment',
                                             'payment_pending', 'paid', 'delivery_pending',
                                             'completed', 'disputed', 'cancelled', 'expired')),
                        FOREIGN KEY(order_id) REFERENCES marketplace_orders(id),
                        FOREIGN KEY(offer_id) REFERENCES marketplace_offers(id)
                    )
                """)
                # Some legacy databases have the permanent per-order unique index
                # but predate the deal lifecycle status column. Preserve those rows
                # with the same safe initial state used by the forward migration below.
                legacy_deal_columns = {
                    row["name"] for row in conn.execute(
                        "PRAGMA table_info(marketplace_deals)"
                    ).fetchall()
                }
                status_expression = (
                    "status" if "status" in legacy_deal_columns
                    else "'payment_not_enabled'"
                )
                conn.execute(f"""
                    INSERT INTO marketplace_deals_new
                    (id, order_id, offer_id, listing_owner_id, offer_sender_id,
                     agreed_amount, currency, created_at, status)
                    SELECT id, order_id, offer_id, listing_owner_id, offer_sender_id,
                           agreed_amount, currency, created_at, {status_expression}
                    FROM marketplace_deals
                """)
                conn.execute("DROP TABLE marketplace_deals")
                conn.execute("ALTER TABLE marketplace_deals_new RENAME TO marketplace_deals")
                conn.execute("""
                    CREATE UNIQUE INDEX idx_marketplace_deals_one_open_reservation
                    ON marketplace_deals(order_id)
                    WHERE status NOT IN ('cancelled', 'expired')
                """)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
        # Forward-compatible migration for databases created before deal lifecycle state.
        deal_columns = {row["name"] for row in conn.execute("PRAGMA table_info(marketplace_deals)").fetchall()}
        if "status" not in deal_columns:
            conn.execute(
                """ALTER TABLE marketplace_deals ADD COLUMN status TEXT NOT NULL
                   DEFAULT 'payment_not_enabled'
                   CHECK(status IN ('payment_not_enabled', 'awaiting_payment', 'payment_pending',
                                    'paid', 'delivery_pending', 'completed', 'disputed',
                                    'cancelled', 'expired'))"""
            )
        # Create this only after status exists: older schemas cannot parse a partial
        # index predicate that references a column they do not yet have.
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_marketplace_deals_one_open_reservation
            ON marketplace_deals(order_id)
            WHERE status NOT IN ('cancelled', 'expired')
        """)

        from marketplace_payments import initialize_sqlite_payment_schema

        initialize_sqlite_payment_schema(conn)


def _require_user(init_data: str | None, verify: Callable[[str], dict]) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="Open HASSLE NFT from Telegram to manage offers")
    return verify(init_data)


def _offer_dict(row: sqlite3.Row, user_id: str) -> dict:
    return {
        "id": row["id"],
        "order_id": row["order_id"],
        "sender_name": row["sender_name"],
        "amount": row["amount"],
        "currency": row["currency"],
        "note": row["note"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "is_mine": row["sender_user_id"] == user_id,
        "order_title": row["order_title"],
        "order_kind": row["order_kind"] if "order_kind" in row.keys() else "listing",
        "order_owner_id": row["order_owner_id"],
        "order_owner_name": row["order_owner_name"],
        "order_status": row["order_status"],
    }


class CreateOffer(BaseModel):
    order_id: str = Field(min_length=4, max_length=64)
    amount: Decimal
    note: str = Field(default="", max_length=300)

    @field_validator("order_id", "note")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0 or value > Decimal("1000000000"):
            raise ValueError("Offer amount must be a positive number")
        return value


class OfferAction(BaseModel):
    action: Literal["accept", "decline", "withdraw", "edit"]
    amount: Decimal | None = None
    note: str | None = Field(default=None, max_length=300)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (not value.is_finite() or value <= 0 or value > Decimal("1000000000")):
            raise ValueError("Offer amount must be a positive number")
        return value

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value


class DealAction(BaseModel):
    action: Literal["cancel"]


class DisputeResolutionExecuteRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


class TonNftDeliveryPrepareRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)

class TelegramBusinessGiftDeliveryRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    accept_transfer_fee: bool = False



class TonNftDeliverySubmittedRequest(BaseModel):
    boc: str = Field(min_length=20, max_length=20000)


class TonNftDeliveryConfirmRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


class StarsCheckoutRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


class TonCheckoutRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


class TonPaymentConfirmRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


class TonRefundExecuteRequest(BaseModel):
    transaction_hash: str = Field(min_length=32, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("transaction_hash")
    @classmethod
    def clean_transaction_hash(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(ch not in "0123456789abcdefABCDEF_-" for ch in cleaned):
            raise ValueError("A valid TON transaction hash is required")
        return cleaned


class DisputeResolutionApprovalRequest(BaseModel):
    decision: Literal["refund_buyer", "release_to_seller"]
    idempotency_key: str = Field(min_length=1, max_length=128)


class DealDisputeRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 10:
            raise ValueError("A dispute reason of at least 10 characters is required")
        return cleaned


def expire_unpaid_marketplace_deals(
    *,
    now: int | None = None,
    ttl_seconds: int | None = None,
    limit: int = 100,
) -> dict[str, int]:
    """Expire only unpaid reservations; confirmed payments are never auto-released."""
    from marketplace_payments import transition_deal_status, transition_payment_intent

    timestamp = int(time.time()) if now is None else int(now)
    if ttl_seconds is None:
        try:
            ttl_seconds = int(os.getenv("HASSLE_DEAL_RESERVATION_TTL_SECONDS", "86400"))
        except ValueError:
            ttl_seconds = 86400
    ttl = max(300, min(int(ttl_seconds), 604800))
    batch_limit = max(1, min(int(limit), 500))
    expired = 0
    released = 0
    skipped_paid = 0
    with _db() as conn:
        candidates = conn.execute(
            """SELECT d.id, d.order_id
               FROM marketplace_deals d
               WHERE d.status IN ('payment_not_enabled', 'awaiting_payment', 'payment_pending')
                 AND COALESCE(
                     (SELECT MAX(pi.expires_at) FROM marketplace_payment_intents pi
                      WHERE pi.deal_id = d.id AND pi.status IN ('created', 'pending')),
                     d.created_at + ?
                 ) <= ?
               ORDER BY d.created_at ASC, d.id ASC
               LIMIT ?""",
            (ttl, timestamp, batch_limit),
        ).fetchall()
        for candidate in candidates:
            deal_id = str(candidate["id"])
            order_id = str(candidate["order_id"])
            begin_write(conn, f"deal:{deal_id}", f"order:{order_id}")
            deal = conn.execute(
                select_for_update(conn, "SELECT * FROM marketplace_deals WHERE id = ?"),
                (deal_id,),
            ).fetchone()
            if deal is None or deal["status"] not in {"payment_not_enabled", "awaiting_payment", "payment_pending"}:
                conn.rollback()
                continue
            intents = conn.execute(
                "SELECT id, status FROM marketplace_payment_intents WHERE deal_id = ?",
                (deal_id,),
            ).fetchall()
            if any(row["status"] in {"confirmed", "refunded"} for row in intents):
                skipped_paid += 1
                conn.rollback()
                continue
            for intent in intents:
                if intent["status"] in {"created", "pending"}:
                    transition_payment_intent(conn, intent["id"], "expired", now=timestamp)
            transition_deal_status(
                conn,
                deal_id=deal_id,
                new_status="expired",
                actor_user_id="system:reservation-timeout",
                idempotency_key=f"{deal_id}:reservation-timeout",
                event_type="deal_expired",
                event_data={"reason": "unpaid_reservation_timeout", "ttl_seconds": ttl},
                now=timestamp,
            )
            released_order = conn.execute(
                """UPDATE marketplace_orders SET status='active', updated_at=?
                   WHERE id=? AND status='matched'""",
                (timestamp, order_id),
            )
            if released_order.rowcount == 1:
                released += 1
            conn.commit()
            expired += 1
    return {"expired_deals": expired, "reservations_released": released, "skipped_confirmed_payment": skipped_paid}


def escalate_stalled_paid_deals(
    *,
    now: int | None = None,
    timeout_seconds: int | None = None,
    limit: int = 100,
) -> dict[str, int]:
    """Move overdue paid deliveries to dispute without releasing or refunding escrow."""
    from marketplace_payments import transition_deal_status

    timestamp = int(time.time()) if now is None else int(now)
    if timeout_seconds is None:
        try:
            timeout_seconds = int(os.getenv("HASSLE_DELIVERY_TIMEOUT_SECONDS", "86400"))
        except ValueError:
            timeout_seconds = 86400
    ttl = max(300, min(int(timeout_seconds), 604800))
    batch_limit = max(1, min(int(limit), 500))
    disputed = 0
    with _db() as conn:
        candidates = conn.execute(
            """SELECT d.id, d.order_id, d.status
               FROM marketplace_deals d
               WHERE d.status IN ('paid', 'delivery_pending')
                 AND COALESCE(
                    (SELECT MAX(e.created_at) FROM marketplace_escrow_events e
                     WHERE e.deal_id=d.id AND e.event_type IN ('payment_confirmed','asset_transfer_requested')),
                    d.created_at
                 ) <= ?
               ORDER BY d.created_at ASC, d.id ASC LIMIT ?""",
            (timestamp - ttl, batch_limit),
        ).fetchall()
        for candidate in candidates:
            deal_id = str(candidate["id"])
            begin_write(conn, f"deal:{deal_id}", f"order:{candidate['order_id']}")
            locked = conn.execute(
                select_for_update(conn, "SELECT status FROM marketplace_deals WHERE id=?"),
                (deal_id,),
            ).fetchone()
            if locked is None or locked["status"] not in {"paid", "delivery_pending"}:
                conn.rollback()
                continue
            try:
                transition_deal_status(
                    conn, deal_id=deal_id, new_status="disputed",
                    actor_user_id="system:delivery-timeout",
                    idempotency_key=f"{deal_id}:delivery-timeout",
                    event_type="deal_disputed",
                    event_data={"reason":"seller_delivery_timeout","timeout_seconds":ttl,
                                "escrow_preserved":True},
                    now=timestamp,
                )
            except Exception:
                conn.rollback()
                continue
            conn.commit()
            disputed += 1
    return {"stalled_deliveries_disputed": disputed}


def create_marketplace_offers_router(
    verify_telegram_init_data: Callable[[str], dict],
    require_admin: Callable[[str | None], dict] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/marketplace", tags=["marketplace-offers"])

    def _admin_user(credential: str | None) -> dict:
        if require_admin is not None:
            return require_admin(credential)
        user = _require_user(credential, verify_telegram_init_data)
        raw_ids = os.getenv("ADMIN_TELEGRAM_IDS", os.getenv("ADMIN_TELEGRAM_ID", ""))
        admin_ids = {part.strip() for part in raw_ids.split(",") if part.strip().isdigit()}
        if str(user["id"]) not in admin_ids:
            raise HTTPException(status_code=403, detail="Administrator access only")
        if len(admin_ids) < 2:
            raise HTTPException(status_code=503, detail="Two distinct dispute administrators must be configured")
        return user

    @router.post("/offers", status_code=201)
    async def create_offer(payload: CreateOffer, x_telegram_init_data: str | None = Header(default=None)):
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        if not _verified_asset_actions_enabled():
            raise HTTPException(
                status_code=409,
                detail="Offers are unavailable until TON Connect and verified inventory assets are enabled",
            )
        actor_id = str(user["id"])
        _enforce_user_rate_limit(actor_id, "create_offer", 15)
        now = int(time.time())
        with _db() as conn:
            begin_write(conn, f"order:{payload.order_id}")
            order = conn.execute(select_for_update(conn, "SELECT * FROM marketplace_orders WHERE id = ?"), (payload.order_id,)).fetchone()
            if order is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Marketplace listing not found")
            if order["kind"] not in {"listing", "buy-order"}:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Unsupported marketplace order type")
            if order["kind"] == "buy-order":
                conn.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="Seller responses to buy requests are temporarily unavailable until a specific verified asset can be bound to the protected deal",
                )
            elif order["user_id"] == actor_id:
                conn.rollback()
                raise HTTPException(status_code=403, detail="You cannot make an offer on your own order")
            if order["currency"] == "XTR" and payload.amount != payload.amount.to_integral_value():
                conn.rollback()
                raise HTTPException(status_code=422, detail="Star offers must be whole numbers")
            if not _test_unverified_assets_allowed():
                if order["kind"] == "buy-order":
                    verified_asset = conn.execute(
                        """SELECT 1 FROM verified_assets
                           WHERE id = ? AND owner_telegram_id = ? AND status = 'verified'""",
                        (seller_listing["asset_id"], actor_id),
                    ).fetchone()
                else:
                    verified_asset = conn.execute(
                        """SELECT 1 FROM verified_assets
                           WHERE id = ? AND owner_telegram_id = ? AND status = 'verified'""",
                        (order["asset_id"], order["user_id"]),
                    ).fetchone()
                if verified_asset is None:
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="Offers require a currently verified asset listing")
            if order["status"] != "active":
                conn.rollback()
                raise HTTPException(status_code=409, detail="This marketplace listing is no longer active")
            if order["user_id"] == actor_id:
                conn.rollback()
                raise HTTPException(status_code=403, detail="You cannot make an offer on your own order")
            pending = conn.execute(
                "SELECT COUNT(*) FROM marketplace_offers WHERE order_id = ? AND sender_user_id = ? AND status = 'pending'",
                (payload.order_id, actor_id),
            ).fetchone()[0]
            if pending:
                conn.rollback()
                raise HTTPException(status_code=409, detail="You already have a pending offer for this order")
            total = conn.execute("SELECT COUNT(*) FROM marketplace_offers WHERE order_id = ?", (payload.order_id,)).fetchone()[0]
            if total >= 100:
                conn.rollback()
                raise HTTPException(status_code=409, detail="This listing has reached its offer limit")
            offer_id = "of-" + uuid.uuid4().hex[:16]
            sender_name = (user.get("first_name") or user.get("username") or "Telegram user")[:64]
            conn.execute("""
                INSERT INTO marketplace_offers
                (id, order_id, sender_user_id, sender_name, amount, currency, note, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """, (offer_id, payload.order_id, actor_id, sender_name, format(payload.amount, "f"),
                  order["currency"], payload.note, now, now))
            conn.commit()
            row = conn.execute("""
                SELECT f.*, o.title AS order_title, o.kind AS order_kind, o.user_id AS order_owner_id,
                       o.owner_name AS order_owner_name, o.status AS order_status
                FROM marketplace_offers f JOIN marketplace_orders o ON o.id = f.order_id WHERE f.id = ?
            """, (offer_id,)).fetchone()
        return {"item": _offer_dict(row, actor_id)}

    @router.get("/offers")
    async def list_offers(
        status: str = Query(default="all", pattern="^(pending|accepted|declined|withdrawn|all)$"),
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=10000),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        if not _verified_asset_actions_enabled():
            _require_user(x_telegram_init_data, verify_telegram_init_data)
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        user_id = str(user["id"])
        _enforce_user_rate_limit(user_id, "list_offers", 60)
        where = "f.sender_user_id = ? OR o.user_id = ?"
        values: list[object] = [user_id, user_id]
        if status != "all":
            where = f"({where}) AND f.status = ?"
            values.append(status)
        with _db() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM marketplace_offers f JOIN marketplace_orders o ON o.id = f.order_id WHERE {where}",
                values,
            ).fetchone()[0]
            rows = conn.execute(f"""
                SELECT f.*, o.title AS order_title, o.kind AS order_kind, o.user_id AS order_owner_id,
                       o.owner_name AS order_owner_name, o.status AS order_status
                FROM marketplace_offers f JOIN marketplace_orders o ON o.id = f.order_id
                WHERE {where} ORDER BY f.created_at DESC, f.id DESC LIMIT ? OFFSET ?
            """, (*values, limit, offset)).fetchall()
        return {"items": [_offer_dict(row, user_id) for row in rows], "total": total, "limit": limit, "offset": offset}

    @router.patch("/offers/{offer_id}")
    async def update_offer(offer_id: str, payload: OfferAction,
                           x_telegram_init_data: str | None = Header(default=None)):
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        if not _verified_asset_actions_enabled():
            raise HTTPException(status_code=409, detail="Offer actions are disabled until verified inventory is enabled")
        actor_id = str(user["id"])
        _enforce_user_rate_limit(actor_id, "update_offer", 30)
        now = int(time.time())
        with _db() as conn:
            initial = conn.execute("SELECT order_id FROM marketplace_offers WHERE id = ?", (offer_id,)).fetchone()
            if initial is None:
                raise HTTPException(status_code=404, detail="Offer not found")
            begin_write(conn, f"order:{initial['order_id']}", f"offer:{offer_id}")
            row = conn.execute(select_for_update(conn, """
                SELECT f.*, o.user_id AS order_owner_id, o.owner_name AS order_owner_name,
                       o.title AS order_title, o.status AS order_status,
                       o.kind AS order_kind, o.asset_id AS order_asset_id,
                       o.currency AS order_currency
                FROM marketplace_offers f JOIN marketplace_orders o ON o.id = f.order_id
                WHERE f.id = ?
            """), (offer_id,)).fetchone()
            if row is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Offer not found")
            if row["status"] != "pending":
                conn.rollback()
                raise HTTPException(status_code=409, detail="This offer is no longer pending")
            action = payload.action
            if action == "edit":
                if actor_id != row["sender_user_id"]:
                    conn.rollback()
                    raise HTTPException(status_code=403, detail="Only the offer author can edit it")
                if row["order_status"] != "active":
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="Offers can only be edited while the listing is active")
                if payload.amount is None:
                    conn.rollback()
                    raise HTTPException(status_code=422, detail="An updated offer amount is required")
                if row["currency"] == "XTR" and payload.amount != payload.amount.to_integral_value():
                    conn.rollback()
                    raise HTTPException(status_code=422, detail="Star offers must be whole numbers")
                changed = conn.execute(
                    """UPDATE marketplace_offers SET amount=?, note=?, updated_at=?
                       WHERE id=? AND status='pending'""",
                    (format(payload.amount, "f"), payload.note if payload.note is not None else row["note"], now, offer_id),
                )
                if changed.rowcount != 1:
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="Offer changed before it could be edited")
                conn.commit()
                updated = conn.execute("""
                    SELECT f.*, o.title AS order_title, o.user_id AS order_owner_id,
                           o.owner_name AS order_owner_name, o.status AS order_status
                    FROM marketplace_offers f JOIN marketplace_orders o ON o.id = f.order_id WHERE f.id = ?
                """, (offer_id,)).fetchone()
                return {"item": _offer_dict(updated, actor_id), "edited": True}
            if action in {"accept", "decline"} and actor_id != row["order_owner_id"]:
                conn.rollback()
                raise HTTPException(status_code=403, detail="Only the order owner can respond to this offer")
            if action == "withdraw" and actor_id != row["sender_user_id"]:
                conn.rollback()
                raise HTTPException(status_code=403, detail="Only the offer author can withdraw it")
            deal_id = None
            if action == "accept":
                if row["order_kind"] == "buy-order":
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="Seller responses to buy requests can be accepted after protected deal execution is enabled")
                if row["order_kind"] != "listing" or row["order_status"] != "active":
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="Only an active seller listing can be reserved")
                if row["currency"] != row["order_currency"]:
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="Offer currency no longer matches the listing")
                if not _test_unverified_assets_allowed():
                    asset = conn.execute(
                        """SELECT id FROM verified_assets
                           WHERE id = ? AND owner_telegram_id = ? AND status = 'verified'""",
                        (row["order_asset_id"], row["order_owner_id"]),
                    ).fetchone()
                    if asset is None:
                        conn.rollback()
                        raise HTTPException(status_code=409, detail="The listing no longer has a verified owner asset")
                open_deal = conn.execute(
                    """SELECT id FROM marketplace_deals
                       WHERE order_id = ? AND status NOT IN ('cancelled', 'expired')""",
                    (row["order_id"],),
                ).fetchone()
                if open_deal is not None:
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="This listing already has an active reservation")
                deal_id = "deal-" + uuid.uuid4().hex[:20]
                conn.execute(
                    """INSERT INTO marketplace_deals
                       (id, order_id, offer_id, listing_owner_id, offer_sender_id,
                        agreed_amount, currency, created_at, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'payment_not_enabled')""",
                    (deal_id, row["order_id"], offer_id, row["order_owner_id"],
                     row["sender_user_id"], row["amount"], row["currency"], now),
                )
                reserved_order = conn.execute(
                    """UPDATE marketplace_orders SET status='matched', updated_at=?
                       WHERE id=? AND status='active'""",
                    (now, row["order_id"]),
                )
                if reserved_order.rowcount != 1:
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="Listing reservation lost a concurrent update")
                accepted_offer = conn.execute(
                    """UPDATE marketplace_offers SET status='accepted', updated_at=?
                       WHERE id=? AND status='pending'""",
                    (now, offer_id),
                )
                if accepted_offer.rowcount != 1:
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="Offer changed before reservation")
                conn.execute(
                    """UPDATE marketplace_offers SET status='declined', updated_at=?
                       WHERE order_id=? AND id<>? AND status='pending'""",
                    (now, row["order_id"], offer_id),
                )
                for event_index, (event_type, event_data) in enumerate((
                    ("deal_created", {"offer_id": offer_id, "payment_status": "not_enabled"}),
                    ("listing_reserved", {"order_id": row["order_id"]}),
                    ("payment_not_enabled", {"currency": row["currency"]}),
                ), start=1):
                    conn.execute(
                        """INSERT INTO marketplace_deal_events
                           (id, deal_id, order_id, actor_user_id, event_type, event_data, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (f"de-{event_index:02d}-" + uuid.uuid4().hex[:20], deal_id, row["order_id"],
                         actor_id, event_type,
                         json.dumps(event_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                         now),
                    )
            else:
                new_status = {"decline": "declined", "withdraw": "withdrawn"}[action]
                conn.execute(
                    "UPDATE marketplace_offers SET status=?, updated_at=? WHERE id=? AND status='pending'",
                    (new_status, now, offer_id),
                )
            conn.commit()
            updated = conn.execute("""
                SELECT f.*, o.title AS order_title, o.user_id AS order_owner_id,
                       o.owner_name AS order_owner_name, o.status AS order_status
                FROM marketplace_offers f JOIN marketplace_orders o ON o.id = f.order_id WHERE f.id = ?
            """, (offer_id,)).fetchone()
            deal_status = None
            if deal_id:
                deal_status = conn.execute(
                    "SELECT status FROM marketplace_deals WHERE id = ?", (deal_id,)
                ).fetchone()["status"]
        result = {"item": _offer_dict(updated, actor_id)}
        if deal_id:
            result.update({
                "deal_id": deal_id,
                "deal_status": deal_status,
                "reservation_created": True,
                "payments_enabled": False,
            })
        return result

    @router.post("/deals/{deal_id}/actions")
    async def act_on_deal(
        deal_id: str,
        payload: DealAction,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Allow participants to release an unpaid marketplace reservation atomically."""
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        if not _verified_asset_actions_enabled():
            raise HTTPException(status_code=409, detail="Deal actions are disabled until verified inventory is enabled")
        actor_id = str(user["id"])
        _enforce_user_rate_limit(actor_id, "deal_action", 10)
        now = int(time.time())
        with _db() as conn:
            initial = conn.execute("SELECT order_id FROM marketplace_deals WHERE id = ?", (deal_id,)).fetchone()
            if initial is None:
                raise HTTPException(status_code=404, detail="Deal not found")
            begin_write(conn, f"order:{initial['order_id']}", f"deal:{deal_id}")
            deal = conn.execute(
                select_for_update(conn, """SELECT d.*, o.status AS order_status
                   FROM marketplace_deals d JOIN marketplace_orders o ON o.id = d.order_id
                   WHERE d.id = ?"""),
                (deal_id,),
            ).fetchone()
            if deal is None or actor_id not in (deal["listing_owner_id"], deal["offer_sender_id"]):
                conn.rollback()
                raise HTTPException(status_code=404, detail="Deal not found")
            if deal["status"] == "cancelled":
                conn.commit()
                return {"deal_id": deal_id, "status": "cancelled", "already_cancelled": True,
                        "payments_enabled": False}
            if deal["status"] != "payment_not_enabled":
                conn.rollback()
                raise HTTPException(status_code=409, detail="Only unpaid marketplace reservations can be cancelled")
            if deal["order_status"] != "matched":
                conn.rollback()
                raise HTTPException(status_code=409, detail="The reserved order is no longer in a cancellable state")
            changed = conn.execute(
                """UPDATE marketplace_deals SET status='cancelled'
                   WHERE id=? AND status='payment_not_enabled'""",
                (deal_id,),
            )
            if changed.rowcount != 1:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Deal state changed; refresh and try again")
            released = conn.execute(
                """UPDATE marketplace_orders SET status='active', updated_at=?
                   WHERE id=? AND status='matched'""",
                (now, deal["order_id"]),
            )
            if released.rowcount != 1:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Reservation could not be released safely")
            conn.execute(
                """INSERT INTO marketplace_deal_lifecycle_events
                   (id, deal_id, order_id, actor_user_id, event_type, event_data, created_at)
                   VALUES (?, ?, ?, ?, 'deal_cancelled', ?, ?)""",
                ("dl-" + uuid.uuid4().hex[:20], deal_id, deal["order_id"], actor_id,
                 json.dumps({"reason": "participant_cancelled_unpaid_reservation",
                             "reservation_released": True, },
                            separators=(",", ":")), now),
            )
            conn.commit()
        return {"deal_id": deal_id, "status": "cancelled", "reservation_released": True,
                "payments_enabled": False}

    @router.post("/deals/{deal_id}/disputes")
    async def open_deal_dispute(
        deal_id: str,
        payload: DealDisputeRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Open a participant dispute only after confirmed payment and before completion."""
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        actor_id = str(user["id"])
        _enforce_user_rate_limit(actor_id, "deal_dispute", 5)
        with _db() as conn:
            initial = conn.execute(
                "SELECT order_id FROM marketplace_deals WHERE id = ?",
                (deal_id,),
            ).fetchone()
            if initial is None:
                raise HTTPException(status_code=404, detail="Deal not found")
            begin_write(conn, f"order:{initial['order_id']}", f"deal:{deal_id}")
            deal = conn.execute(
                select_for_update(conn, "SELECT * FROM marketplace_deals WHERE id = ?"),
                (deal_id,),
            ).fetchone()
            if deal is None or actor_id not in (deal["listing_owner_id"], deal["offer_sender_id"]):
                conn.rollback()
                raise HTTPException(status_code=404, detail="Deal not found")
            if deal["status"] not in {"paid", "delivery_pending", "disputed"}:
                conn.rollback()
                raise HTTPException(status_code=409, detail="A dispute can only be opened after payment confirmation and before completion")
            from marketplace_payments import transition_deal_status
            try:
                result = transition_deal_status(
                    conn,
                    deal_id=deal_id,
                    new_status="disputed",
                    actor_user_id=actor_id,
                    idempotency_key=payload.idempotency_key,
                    event_type="deal_disputed",
                    event_data={"reason": payload.reason},
                    now=int(time.time()),
                )
            except Exception:
                conn.rollback()
                raise
            conn.commit()
        return {"deal_id": deal_id, "status": "disputed", "idempotent_replay": result["idempotent_replay"]}


    @router.get("/deals/{deal_id}/events")
    async def list_deal_events(
        deal_id: str,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Return the immutable deal timeline to its seller and buyer only."""
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        actor_id = str(user["id"])
        _enforce_user_rate_limit(actor_id, "deal_events", 60)
        with _db() as conn:
            deal = conn.execute(
                """SELECT listing_owner_id, offer_sender_id
                   FROM marketplace_deals WHERE id = ?""",
                (deal_id,),
            ).fetchone()
            # Hide both missing deals and deals belonging to somebody else.
            if deal is None or actor_id not in (
                deal["listing_owner_id"], deal["offer_sender_id"]
            ):
                raise HTTPException(status_code=404, detail="Deal not found")
            rows = conn.execute(
                """SELECT id, actor_user_id, event_type, event_data, created_at
                   FROM marketplace_deal_events WHERE deal_id = ?
                   UNION ALL
                   SELECT id, actor_user_id, event_type, event_data, created_at
                   FROM marketplace_deal_lifecycle_events WHERE deal_id = ?
                   ORDER BY created_at ASC, id ASC""",
                (deal_id, deal_id),
            ).fetchall()

        items = []
        for row in rows:
            try:
                event_data = json.loads(row["event_data"] or "{}")
            except (TypeError, ValueError):
                event_data = {}
            if not isinstance(event_data, dict):
                event_data = {}
            items.append({
                "id": row["id"],
                "actor_user_id": row["actor_user_id"],
                "type": row["event_type"],
                "data": event_data,
                "created_at": row["created_at"],
            })
        with _db() as conn:
            escrow_rows = list_escrow_events(conn, deal_id)
        items.extend({
            "id": row["id"],
            "actor_user_id": row["actor_user_id"],
            "type": row["event_type"],
            "data": row["event_data"],
            "created_at": row["created_at"],
        } for row in escrow_rows)
        items.sort(key=lambda item: (item["created_at"], item["id"]))
        return {"deal_id": deal_id, "items": items, "total": len(items)}

    @router.post("/admin/deals/{deal_id}/dispute-approvals")
    async def approve_dispute_resolution(
        deal_id: str,
        payload: DisputeResolutionApprovalRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        admin = _admin_user(x_telegram_init_data)
        admin_id = str(admin["id"])
        _enforce_user_rate_limit(admin_id, "dispute_resolution_approval", 10)
        raw_ids = os.getenv("ADMIN_TELEGRAM_IDS", os.getenv("ADMIN_TELEGRAM_ID", ""))
        configured_admins = {part.strip() for part in raw_ids.split(",") if part.strip().isdigit()}
        if len(configured_admins) < 2:
            raise HTTPException(status_code=503, detail="Two distinct dispute administrators must be configured")
        with _db() as conn:
            begin_write(conn, f"deal:{deal_id}")
            deal = conn.execute(
                select_for_update(conn, "SELECT status FROM marketplace_deals WHERE id = ?"),
                (deal_id,),
            ).fetchone()
            if deal is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Deal not found")
            if deal["status"] != "disputed":
                conn.rollback()
                raise HTTPException(status_code=409, detail="Only disputed deals can receive resolution approvals")
            from marketplace_payments import PaymentConflict, PaymentStateError, record_dispute_resolution_approval
            try:
                result = record_dispute_resolution_approval(
                    conn,
                    deal_id=deal_id,
                    admin_user_id=admin_id,
                    decision=payload.decision,
                    idempotency_key=payload.idempotency_key,
                    now=int(time.time()),
                )
            except (PaymentConflict, PaymentStateError) as exc:
                conn.rollback()
                raise HTTPException(status_code=409, detail=str(exc))
            except Exception:
                conn.rollback()
                raise
            conn.commit()
        return {
            "deal_id": deal_id,
            "decision": payload.decision,
            "approvals": result["approvals"],
            "required": 2,
            "ready_for_execution": result["ready_for_execution"],
            "execution_enabled": False,
            "idempotent_replay": result["idempotent_replay"],
        }


    @router.post("/deals/{deal_id}/stars-checkout")
    async def create_stars_checkout(
        deal_id: str,
        payload: StarsCheckoutRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Create a Telegram Stars invoice only when the operator explicitly enables custody."""
        import hashlib
        import httpx
        from marketplace_payments import append_escrow_event, create_payment_intent, transition_deal_status, transition_payment_intent

        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        buyer_id = str(user["id"])
        _enforce_user_rate_limit(buyer_id, "stars_checkout", 5)
        if os.getenv("HASSLE_STARS_CHECKOUT_ENABLED", "0") != "1":
            raise HTTPException(status_code=503, detail="Telegram Stars checkout is not enabled")
        if os.getenv("HASSLE_STARS_CUSTODY_ACKNOWLEDGED", "0") != "1":
            raise HTTPException(status_code=503, detail="Stars custody policy has not been acknowledged")
        if os.getenv("HASSLE_STARS_PAYOUT_MODE", "") != "internal_xtr_credit":
            raise HTTPException(status_code=503, detail="Stars seller-credit policy is not configured")
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        support_contact = os.getenv("PAYMENT_SUPPORT_CONTACT", "").strip()
        if not bot_token or not support_contact:
            raise HTTPException(status_code=503, detail="Payment bot token or payment support contact is not configured")
        now = int(time.time())
        try:
            ttl = int(os.getenv("HASSLE_STARS_INVOICE_TTL_SECONDS", "900"))
        except ValueError:
            ttl = 900
        ttl = max(300, min(ttl, 3600))
        invoice_payload = "hassle:stars:" + hashlib.sha256(
            f"{deal_id}:{payload.idempotency_key}".encode("utf-8")
        ).hexdigest()[:40]
        with _db() as conn:
            begin_write(conn, f"deal:{deal_id}")
            deal = conn.execute(
                select_for_update(conn, """SELECT d.*, o.status AS listing_status, o.currency AS listing_currency,
                    o.asset_id AS listing_asset_id
                    FROM marketplace_deals d
                    JOIN marketplace_orders o ON o.id = d.order_id
                    WHERE d.id = ?"""),
                (deal_id,),
            ).fetchone()
            if deal is None or buyer_id != str(deal["offer_sender_id"]):
                conn.rollback()
                raise HTTPException(status_code=404, detail="Deal not found")
            if (
                deal["currency"] != "XTR"
                or deal["listing_currency"] != "XTR"
                or deal["listing_status"] != "matched"
                or deal["status"] not in {"payment_not_enabled", "awaiting_payment"}
            ):
                conn.rollback()
                raise HTTPException(status_code=409, detail="Deal is not eligible for Telegram Stars checkout")
            amount = int(Decimal(str(deal["agreed_amount"])))
            if amount <= 0 or Decimal(str(deal["agreed_amount"])) != Decimal(amount):
                conn.rollback()
                raise HTTPException(status_code=409, detail="Telegram Stars amount must be a positive whole number")
            if not _test_unverified_assets_allowed():
                verified = conn.execute(
                    """SELECT 1 FROM verified_assets
                       WHERE id = ? AND owner_telegram_id = ? AND status = 'verified'""",
                    (deal["listing_asset_id"], deal["listing_owner_id"]),
                ).fetchone()
                if verified is None:
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="The seller asset is not currently verified")
            if deal["status"] == "payment_not_enabled":
                transition_deal_status(
                    conn,
                    deal_id=deal_id,
                    new_status="awaiting_payment",
                    actor_user_id=buyer_id,
                    idempotency_key=f"{deal_id}:stars-checkout-request:{payload.idempotency_key}",
                    event_type="deal_payment_requested",
                    event_data={"provider": "telegram_stars", "currency": "XTR"},
                    now=now,
                )
            intent = conn.execute(
                """SELECT * FROM marketplace_payment_intents
                   WHERE deal_id = ? AND status IN ('created', 'pending')
                   ORDER BY created_at DESC LIMIT 1""",
                (deal_id,),
            ).fetchone()
            if intent is not None and int(intent["expires_at"]) <= now:
                transition_payment_intent(conn, intent["id"], "expired", now=now)
                intent = None
            if intent is None:
                intent = create_payment_intent(
                    conn,
                    deal_id=deal_id,
                    provider="telegram_stars",
                    currency="XTR",
                    amount=amount,
                    idempotency_key=f"stars:{deal_id}:{payload.idempotency_key}",
                    expires_at=now + ttl,
                    invoice_payload=invoice_payload,
                    metadata={
                        "buyer_telegram_id": buyer_id,
                        "seller_telegram_id": str(deal["listing_owner_id"]),
                        "deal_id": deal_id,
                        "payout_mode": "internal_xtr_credit",
                    },
                    now=now,
                )
            else:
                intent = dict(intent)
            invoice_payload = str(intent["invoice_payload"])
            append_escrow_event(
                conn,
                deal_id=deal_id,
                actor_user_id=buyer_id,
                event_type="payment_intent_created",
                event_key=f"{deal_id}:stars-intent:{intent['id']}",
                event_data={"provider": "telegram_stars", "payment_intent_id": intent["id"], "amount": str(amount), "currency": "XTR"},
                now=now,
            )
            conn.commit()
        invoice_request = {
            "title": "HASSLE NFT marketplace purchase",
            "description": f"Payment for HASSLE NFT deal {deal_id}",
            "payload": invoice_payload,
            "currency": "XTR",
            "prices": [{"label": "HASSLE NFT purchase", "amount": amount}],
        }
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{bot_token}/createInvoiceLink",
                    json=invoice_request,
                )
                response.raise_for_status()
                provider_result = response.json()
            if not provider_result.get("ok") or not isinstance(provider_result.get("result"), str):
                raise RuntimeError("Telegram did not return an invoice link")
        except Exception:
            with _db() as conn:
                begin_write(conn, f"payment-intent:{intent['id']}")
                transition_payment_intent(conn, intent["id"], "failed", now=int(time.time()))
                conn.commit()
            raise HTTPException(status_code=502, detail="Telegram could not create the Stars invoice")
        return {
            "deal_id": deal_id,
            "payment_intent_id": intent["id"],
            "provider": "telegram_stars",
            "currency": "XTR",
            "amount": str(amount),
            "invoice_url": provider_result["result"],
            "expires_at": intent["expires_at"],
        }


    @router.post("/admin/deals/{deal_id}/execute-refund")
    async def execute_approved_stars_refund(
        deal_id: str,
        payload: DisputeResolutionExecuteRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Execute a two-admin-approved Telegram Stars refund and reverse the escrow journal."""
        import httpx
        admin = _admin_user(x_telegram_init_data)
        admin_id = str(admin["id"])
        _enforce_user_rate_limit(admin_id, "stars_refund_execution", 5)
        configured_admins = {
            part.strip()
            for part in os.getenv("ADMIN_TELEGRAM_IDS", os.getenv("ADMIN_TELEGRAM_ID", "")).split(",")
            if part.strip().isdigit()
        }
        if len(configured_admins) < 2:
            raise HTTPException(status_code=503, detail="Two distinct dispute administrators must be configured")
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        if not bot_token:
            raise HTTPException(status_code=503, detail="Telegram payment bot is not configured")
        with _db() as conn:
            begin_write(conn, f"deal:{deal_id}")
            deal = conn.execute(
                select_for_update(conn, "SELECT * FROM marketplace_deals WHERE id = ?"),
                (deal_id,),
            ).fetchone()
            if deal is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Deal not found")
            if deal["status"] == "cancelled":
                completed = conn.execute(
                    """SELECT event_data FROM marketplace_escrow_events
                       WHERE deal_id = ? AND event_type = 'refund_confirmed'
                       ORDER BY created_at DESC LIMIT 1""",
                    (deal_id,),
                ).fetchone()
                if completed is not None:
                    refund_data = json.loads(completed["event_data"] or "{}")
                    refunded_intent = conn.execute(
                        """SELECT status FROM marketplace_payment_intents
                           WHERE deal_id=? AND provider='telegram_stars'
                           ORDER BY created_at DESC LIMIT 1""",
                        (deal_id,),
                    ).fetchone()
                    if refund_data.get("provider") == "telegram_stars" and refunded_intent is not None and refunded_intent["status"] == "refunded":
                        conn.rollback()
                        return {"deal_id": deal_id, "status": "cancelled", "refund_status": "refunded", "idempotent_replay": True}
                conn.rollback()
                raise HTTPException(status_code=409, detail="Deal was cancelled without a confirmed Telegram Stars refund")
            if deal["status"] != "disputed":
                raise HTTPException(status_code=409, detail="Only a disputed deal can execute a resolution")
            approval_rows = conn.execute(
                """SELECT admin_user_id FROM marketplace_dispute_resolution_approvals
                   WHERE deal_id = ? AND decision = 'refund_buyer'""",
                (deal_id,),
            ).fetchall()
            all_approvals = int(conn.execute(
                "SELECT COUNT(*) FROM marketplace_dispute_resolution_approvals WHERE deal_id = ?",
                (deal_id,),
            ).fetchone()[0])
            approval_admins = {str(row["admin_user_id"]) for row in approval_rows}
            if (
                len(approval_rows) != 2
                or all_approvals != 2
                or len(approval_admins) != 2
                or not approval_admins.issubset(configured_admins)
            ):
                raise HTTPException(status_code=409, detail="Two matching approvals from currently configured administrators are required")
            intent = conn.execute(
                """SELECT * FROM marketplace_payment_intents
                   WHERE deal_id = ? AND provider = 'telegram_stars'
                   ORDER BY created_at DESC LIMIT 1""",
                (deal_id,),
            ).fetchone()
            if intent is None or intent["status"] not in {"confirmed", "refunded"} or not intent["provider_reference"]:
                raise HTTPException(status_code=409, detail="A confirmed Telegram Stars payment is required")
            buyer_id = str(deal["offer_sender_id"])
            charge_id = str(intent["provider_reference"])
            amount = str(intent["amount"])
            requested_event = conn.execute(
                """SELECT event_key FROM marketplace_escrow_events
                   WHERE deal_id = ? AND event_type = 'refund_requested' LIMIT 1""",
                (deal_id,),
            ).fetchone()
            expected_refund_key = f"{deal_id}:stars-refund-requested:{payload.idempotency_key}"
            if requested_event is not None and requested_event["event_key"] != expected_refund_key:
                conn.rollback()
                raise HTTPException(status_code=409, detail="A refund is already in progress; retry with the original idempotency key")
            from marketplace_payments import append_escrow_event
            append_escrow_event(
                conn,
                deal_id=deal_id,
                actor_user_id=admin_id,
                event_type="refund_requested",
                event_key=expected_refund_key,
                event_data={"provider": "telegram_stars", "provider_reference": charge_id, "amount": amount, "currency": "XTR"},
                now=int(time.time()),
            )
            conn.commit()
        provider_already_refunded = False
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{bot_token}/refundStarPayment",
                    json={"user_id": int(buyer_id), "telegram_payment_charge_id": charge_id},
                )
                provider_result = response.json()
                if response.status_code >= 400:
                    description = str(provider_result.get("description") or "")
                    if "CHARGE_ALREADY_REFUNDED" in description:
                        provider_already_refunded = True
                    else:
                        raise RuntimeError("Telegram rejected the Stars refund")
                elif provider_result.get("ok") is not True or provider_result.get("result") is not True:
                    description = str(provider_result.get("description") or "")
                    if "CHARGE_ALREADY_REFUNDED" in description:
                        provider_already_refunded = True
                    else:
                        raise RuntimeError("Telegram did not confirm the Stars refund")
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Telegram refund could not be confirmed; the deal remains disputed") from exc
        now = int(time.time())
        with _db() as conn:
            begin_write(conn, f"deal:{deal_id}", f"payment-intent:{intent['id']}")
            locked = conn.execute(
                select_for_update(conn, "SELECT status FROM marketplace_deals WHERE id = ?"),
                (deal_id,),
            ).fetchone()
            locked_intent = conn.execute(
                select_for_update(conn, "SELECT status FROM marketplace_payment_intents WHERE id = ?"),
                (intent["id"],),
            ).fetchone()
            if locked is None or locked_intent is None:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Deal or payment intent changed during refund")
            if locked["status"] == "cancelled" and locked_intent["status"] == "refunded":
                conn.rollback()
                return {"deal_id": deal_id, "status": "cancelled", "refund_status": "refunded", "idempotent_replay": True}
            from marketplace_payments import PaymentConflict, PaymentStateError, post_ledger_transaction, transition_deal_status, transition_payment_intent
            try:
                if locked_intent["status"] == "confirmed":
                    transition_payment_intent(conn, intent["id"], "refunded", now=now)
                ledger = post_ledger_transaction(
                    conn,
                    idempotency_key=f"{deal_id}:stars-refund:{charge_id}",
                    reference_type="refund",
                    reference_id=deal_id,
                    currency="XTR",
                    debit_account=("escrow", deal_id),
                    credit_account=("telegram_stars_clearing", charge_id),
                    debit_amount=amount,
                    credit_amount=amount,
                    now=now,
                )
                transition_deal_status(
                    conn,
                    deal_id=deal_id,
                    new_status="cancelled",
                    actor_user_id=admin_id,
                    idempotency_key=f"{deal_id}:stars-refund-confirmed:{charge_id}",
                    event_type="refund_confirmed",
                    event_data={
                        "provider": "telegram_stars",
                        "provider_reference": charge_id,
                        "payment_intent_id": intent["id"],
                        "ledger_transaction_id": ledger["id"],
                        "currency": "XTR",
                        "amount": amount,
                        "provider_already_refunded": provider_already_refunded,
                    },
                    now=now,
                )
            except (PaymentConflict, PaymentStateError) as exc:
                conn.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            conn.commit()
        return {"deal_id": deal_id, "status": "cancelled", "refund_status": "refunded", "currency": "XTR", "amount": amount, "idempotent_replay": False}


    @router.post("/admin/deals/{deal_id}/execute-ton-refund")
    async def execute_approved_ton_refund(
        deal_id: str,
        payload: TonRefundExecuteRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Record a manual treasury refund only after TON independently confirms the outgoing transfer."""
        import hashlib
        import httpx
        from decimal import Decimal
        from ton_nft_inventory import normalise_ton_address
        from marketplace_payments import PaymentConflict, PaymentStateError, append_escrow_event, post_ledger_transaction, record_payment_event, transition_deal_status, transition_payment_intent

        admin = _admin_user(x_telegram_init_data)
        admin_id = str(admin["id"])
        _enforce_user_rate_limit(admin_id, "ton_refund_execution", 5)
        configured_admins = {
            part.strip() for part in os.getenv("ADMIN_TELEGRAM_IDS", os.getenv("ADMIN_TELEGRAM_ID", "")).split(",")
            if part.strip().isdigit()
        }
        if len(configured_admins) < 2:
            raise HTTPException(status_code=503, detail="Two distinct dispute administrators must be configured")
        token = os.getenv("TONAPI_TOKEN", "").strip()
        if not token:
            raise HTTPException(status_code=503, detail="TON transaction verification provider is not configured")
        with _db() as conn:
            begin_write(conn, f"deal:{deal_id}")
            deal = conn.execute(
                select_for_update(conn, "SELECT * FROM marketplace_deals WHERE id=?"),
                (deal_id,),
            ).fetchone()
            if deal is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Deal not found")
            if deal["status"] == "cancelled":
                refund_event = conn.execute(
                    """SELECT event_data FROM marketplace_escrow_events
                       WHERE deal_id=? AND event_type='refund_confirmed' ORDER BY created_at DESC LIMIT 1""",
                    (deal_id,),
                ).fetchone()
                if refund_event is not None:
                    refund_data = json.loads(refund_event["event_data"] or "{}")
                    if refund_data.get("provider") == "ton":
                        refunded_intent = conn.execute(
                            """SELECT status FROM marketplace_payment_intents
                               WHERE deal_id=? AND provider='ton' ORDER BY created_at DESC LIMIT 1""",
                            (deal_id,),
                        ).fetchone()
                        if refunded_intent is not None and refunded_intent["status"] == "refunded":
                            if refund_data.get("provider_reference") == payload.transaction_hash:
                                conn.rollback()
                                return {
                                    "deal_id": deal_id,
                                    "status": "cancelled",
                                    "refund_status": "refunded",
                                    "idempotent_replay": True,
                                }
                            conn.rollback()
                            raise HTTPException(status_code=409, detail="A different TON refund transaction was already recorded")
                conn.rollback()
                raise HTTPException(status_code=409, detail="Deal was cancelled without a confirmed TON refund")
            if deal["status"] != "disputed":
                conn.rollback()
                raise HTTPException(status_code=409, detail="Only disputed deals can be refunded")
            approvals = conn.execute(
                """SELECT admin_user_id FROM marketplace_dispute_resolution_approvals
                   WHERE deal_id=? AND decision='refund_buyer'""",
                (deal_id,),
            ).fetchall()
            all_count = int(conn.execute(
                "SELECT COUNT(*) FROM marketplace_dispute_resolution_approvals WHERE deal_id=?",
                (deal_id,),
            ).fetchone()[0])
            approvers = {str(row["admin_user_id"]) for row in approvals}
            if len(approvals) != 2 or all_count != 2 or len(approvers) != 2 or not approvers.issubset(configured_admins):
                conn.rollback()
                raise HTTPException(status_code=409, detail="Two matching refund approvals from distinct configured administrators are required")
            intent = conn.execute(
                """SELECT * FROM marketplace_payment_intents
                   WHERE deal_id=? AND provider='ton' ORDER BY created_at DESC LIMIT 1""",
                (deal_id,),
            ).fetchone()
            if intent is None or intent["status"] not in {"confirmed", "refunded"} or not intent["provider_reference"]:
                conn.rollback()
                raise HTTPException(status_code=409, detail="A confirmed TON payment is required")
            if intent["status"] == "refunded":
                refund_event = conn.execute(
                    """SELECT event_data FROM marketplace_escrow_events
                       WHERE deal_id=? AND event_type='refund_confirmed' ORDER BY created_at DESC LIMIT 1""",
                    (deal_id,),
                ).fetchone()
                refund_data = json.loads(refund_event["event_data"] or "{}") if refund_event is not None else {}
                if refund_data.get("provider") == "ton" and refund_data.get("provider_reference") == payload.transaction_hash:
                    conn.rollback()
                    return {"deal_id": deal_id, "status": "cancelled", "refund_status": "refunded", "idempotent_replay": True}
                conn.rollback()
                raise HTTPException(status_code=409, detail="A different refund has already been recorded for this TON payment")
            metadata = json.loads(intent["metadata_json"] or "{}")
            treasury = str(metadata.get("treasury") or "")
            buyer_wallet = str(metadata.get("buyer_wallet") or "")
            amount = str(intent["amount"])
            intent_id = str(intent["id"])
            conn.commit()
        try:
            treasury_raw = normalise_ton_address(treasury)
            buyer_raw = normalise_ton_address(buyer_wallet)
            base_url = os.getenv("TONAPI_BASE_URL", "https://tonapi.io").rstrip("/")
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
                response = await client.get(
                    f"{base_url}/v2/blockchain/transactions/{payload.transaction_hash}",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
                response.raise_for_status()
                transaction = response.json()
            if not isinstance(transaction, dict) or transaction.get("success") is not True:
                raise HTTPException(status_code=409, detail="TON has not confirmed a successful refund transaction")
            account_value = transaction.get("account")
            if isinstance(account_value, dict):
                account_value = account_value.get("address")
            if normalise_ton_address(str(account_value or "")) != treasury_raw:
                raise HTTPException(status_code=409, detail="Refund transaction was not sent by the configured treasury")
            expected_nano = int(Decimal(amount) * Decimal(1_000_000_000))
            refunded = False
            for message in transaction.get("out_msgs", []) if isinstance(transaction.get("out_msgs"), list) else []:
                if not isinstance(message, dict) or message.get("bounced") is True:
                    continue
                try:
                    destination = normalise_ton_address(str(message.get("destination") or ""))
                    value = int(message.get("value") or 0)
                except Exception:
                    continue
                if destination == buyer_raw and value == expected_nano:
                    refunded = True
                    break
            if not refunded:
                raise HTTPException(status_code=409, detail="TON transaction does not contain the exact approved refund to the buyer")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="TON refund transaction could not be verified") from exc
        now = int(time.time())
        with _db() as conn:
            begin_write(conn, f"deal:{deal_id}", f"payment-intent:{intent_id}")
            locked = conn.execute(
                select_for_update(conn, "SELECT * FROM marketplace_payment_intents WHERE id=?"),
                (intent_id,),
            ).fetchone()
            locked_deal = conn.execute(
                select_for_update(conn, "SELECT * FROM marketplace_deals WHERE id=?"),
                (deal_id,),
            ).fetchone()
            if locked is None or locked_deal is None or locked["status"] != "confirmed" or locked_deal["status"] != "disputed":
                conn.rollback()
                raise HTTPException(status_code=409, detail="Payment or dispute state changed during refund verification")
            tx_hash = payload.transaction_hash
            event_hash = hashlib.sha256(f"{deal_id}:{tx_hash}:{buyer_raw}:{expected_nano}".encode()).hexdigest()
            try:
                record_payment_event(
                    conn, provider="ton", provider_event_id=tx_hash,
                    payment_intent_id=intent_id, event_type="refund_confirmed",
                    payload_hash=event_hash, now=now,
                )
                transition_payment_intent(conn, intent_id, "refunded", now=now)
                ledger = post_ledger_transaction(
                    conn, idempotency_key=f"{deal_id}:ton-refund:{tx_hash}",
                    reference_type="refund", reference_id=deal_id, currency="TON",
                    debit_account=("escrow", deal_id), credit_account=("ton_treasury_clearing", tx_hash),
                    debit_amount=amount, credit_amount=amount, now=now,
                )
                transition_deal_status(
                    conn, deal_id=deal_id, new_status="cancelled", actor_user_id=admin_id,
                    idempotency_key=f"{deal_id}:ton-refund-confirmed:{tx_hash}",
                    event_type="refund_confirmed",
                    event_data={"provider":"ton", "provider_reference":tx_hash, "payment_intent_id":intent_id,
                                "ledger_transaction_id":ledger["id"], "currency":"TON", "amount":amount},
                    now=now,
                )
            except (PaymentConflict, PaymentStateError) as exc:
                conn.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            conn.commit()
        return {"deal_id":deal_id, "status":"cancelled", "refund_status":"refunded", "currency":"TON", "amount":amount}


    @router.post("/admin/deals/{deal_id}/execute-seller-release")
    async def execute_approved_seller_release(
        deal_id: str,
        payload: DisputeResolutionExecuteRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Release escrow to the seller only after two admins approve and TON confirms the buyer owns the NFT."""
        import hashlib
        import httpx
        from ton_nft_inventory import fetch_nft_owner, normalise_ton_address

        admin = _admin_user(x_telegram_init_data)
        admin_id = str(admin["id"])
        _enforce_user_rate_limit(admin_id, "seller_release_execution", 5)
        configured_admins = {
            part.strip()
            for part in os.getenv("ADMIN_TELEGRAM_IDS", os.getenv("ADMIN_TELEGRAM_ID", "")).split(",")
            if part.strip().isdigit()
        }
        if len(configured_admins) < 2:
            raise HTTPException(status_code=503, detail="Two distinct dispute administrators must be configured")
        with _db() as conn:
            begin_write(conn, f"deal:{deal_id}")
            deal = conn.execute(
                select_for_update(conn, """SELECT d.*, o.asset_id, a.source, a.external_asset_id,
                    a.owner_telegram_id, a.status AS asset_status
                    FROM marketplace_deals d
                    JOIN marketplace_orders o ON o.id = d.order_id
                    JOIN verified_assets a ON a.id = o.asset_id
                    WHERE d.id = ?"""),
                (deal_id,),
            ).fetchone()
            if deal is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Deal not found")
            if deal["status"] == "completed":
                credit_events = conn.execute(
                    "SELECT event_data FROM marketplace_escrow_events WHERE deal_id=? AND event_type='seller_credit_recorded'",
                    (deal_id,),
                ).fetchall()
                seller_release_was_recorded = any(
                    json.loads(row["event_data"] or "{}").get("asset_ownership") == "buyer_confirmed_on_ton"
                    for row in credit_events
                )
                if seller_release_was_recorded:
                    conn.rollback()
                    return {
                        "deal_id": deal_id,
                        "status": "completed",
                        "seller_credit": "recorded",
                        "settlement_status": "pending_manual_payout",
                        "idempotent_replay": True,
                    }
            if deal["status"] != "disputed":
                conn.rollback()
                raise HTTPException(status_code=409, detail="Only disputed deals can execute a seller release")
            if deal["source"] != "ton_nft" or deal["asset_status"] != "verified" or str(deal["owner_telegram_id"]) != str(deal["listing_owner_id"]):
                conn.rollback()
                raise HTTPException(status_code=409, detail="Seller asset is not eligible for independently verified release")
            approvals = conn.execute(
                """SELECT admin_user_id FROM marketplace_dispute_resolution_approvals
                   WHERE deal_id=? AND decision='release_to_seller'""",
                (deal_id,),
            ).fetchall()
            all_count = int(conn.execute(
                "SELECT COUNT(*) FROM marketplace_dispute_resolution_approvals WHERE deal_id=?",
                (deal_id,),
            ).fetchone()[0])
            approvers = {str(row["admin_user_id"]) for row in approvals}
            if len(approvals) != 2 or all_count != 2 or len(approvers) != 2 or not approvers.issubset(configured_admins):
                conn.rollback()
                raise HTTPException(status_code=409, detail="Two matching approvals from distinct configured administrators are required")
            intent = conn.execute(
                """SELECT * FROM marketplace_payment_intents
                   WHERE deal_id=? AND status='confirmed'
                   ORDER BY created_at DESC LIMIT 1""",
                (deal_id,),
            ).fetchone()
            if intent is None:
                conn.rollback()
                raise HTTPException(status_code=409, detail="A confirmed payment is required before seller release")
            buyer_id = str(deal["offer_sender_id"])
            buyer_wallet = conn.execute(
                """SELECT address FROM verified_wallets
                   WHERE telegram_id=? AND network='-239' AND revoked_at IS NULL
                   ORDER BY last_verified_at DESC LIMIT 1""",
                (buyer_id,),
            ).fetchone()
            if buyer_wallet is None:
                conn.rollback()
                raise HTTPException(status_code=409, detail="The buyer must link and verify a TON mainnet wallet")
            conn.commit()
        try:
            owner = await fetch_nft_owner(str(deal["external_asset_id"]))
            buyer_raw = normalise_ton_address(buyer_wallet["address"])
            if owner.get("is_scam") or normalise_ton_address(str(owner.get("owner_address") or "")) != buyer_raw:
                raise HTTPException(status_code=409, detail="TON does not confirm that the buyer owns the NFT")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="TON ownership verification is temporarily unavailable") from exc
        now = int(time.time())
        ownership_reference = hashlib.sha256(
            f"hassle-dispute-seller-release:{deal_id}:{deal['asset_id']}:{buyer_id}:{buyer_raw}".encode("utf-8")
        ).hexdigest()
        with _db() as conn:
            begin_write(conn, f"deal:{deal_id}", f"asset:{deal['asset_id']}")
            locked = conn.execute(
                select_for_update(conn, "SELECT * FROM marketplace_deals WHERE id=?"),
                (deal_id,),
            ).fetchone()
            asset = conn.execute(
                select_for_update(conn, "SELECT * FROM verified_assets WHERE id=?"),
                (deal["asset_id"],),
            ).fetchone()
            if locked is None or locked["status"] != "disputed" or asset is None:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Deal or asset state changed during seller release")
            if str(asset["owner_telegram_id"]) != str(locked["listing_owner_id"]) or asset["status"] != "verified":
                conn.rollback()
                raise HTTPException(status_code=409, detail="Asset ownership record changed during seller release")
            from marketplace_payments import PaymentConflict, PaymentStateError, append_escrow_event, post_ledger_transaction, transition_deal_status
            try:
                conn.execute(
                    """INSERT INTO asset_owner_transfer_authorizations
                       (asset_id, previous_owner_id, new_owner_id, verification_reference_hash, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (deal["asset_id"], str(locked["listing_owner_id"]), buyer_id, ownership_reference, now),
                )
                changed = conn.execute(
                    """UPDATE verified_assets
                       SET owner_telegram_id=?, verification_method='ton_connect_proof',
                           verification_reference_hash=?, verified_at=?, status='verified', revoked_at=NULL
                       WHERE id=? AND owner_telegram_id=? AND status='verified'""",
                    (buyer_id, ownership_reference, now, deal["asset_id"], str(locked["listing_owner_id"])),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("Dispute seller-release ownership update did not match")
                conn.execute(
                    """INSERT INTO asset_ownership_history
                       (asset_id, previous_owner_id, new_owner_id, verification_reference_hash, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (deal["asset_id"], str(locked["listing_owner_id"]), buyer_id, ownership_reference, now),
                )
                ledger = post_ledger_transaction(
                    conn,
                    idempotency_key=f"{deal_id}:seller-release:{intent['provider_reference']}",
                    reference_type="deal",
                    reference_id=deal_id,
                    currency=str(intent["currency"]),
                    debit_account=("escrow", deal_id),
                    credit_account=("seller_payable", str(deal["listing_owner_id"])),
                    debit_amount=intent["amount"],
                    credit_amount=intent["amount"],
                    now=now,
                )
                transition_deal_status(
                    conn, deal_id=deal_id, new_status="completed", actor_user_id=admin_id,
                    idempotency_key=f"{deal_id}:seller-release-confirmed:{payload.idempotency_key}",
                    event_type="seller_credit_recorded",
                    event_data={"payment_intent_id": intent["id"], "ledger_transaction_id": ledger["id"],
                                "currency": str(intent["currency"]), "amount": str(intent["amount"]),
                                "seller_telegram_id": str(deal["listing_owner_id"]),
                                "settlement_status": "pending_manual_payout",
                                "asset_ownership": "buyer_confirmed_on_ton"},
                    now=now,
                )
            except (PaymentConflict, PaymentStateError) as exc:
                conn.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            conn.commit()
        return {"deal_id": deal_id, "status": "completed", "seller_credit": "recorded", "settlement_status": "pending_manual_payout", "idempotent_replay": False}


    @router.post("/admin/deals/{deal_id}/execute-ton-seller-payout")
    async def execute_confirmed_ton_seller_payout(
        deal_id: str,
        payload: TonRefundExecuteRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Record a manual TON multisig payout only after TONAPI confirms the exact treasury transfer."""
        import hashlib
        import httpx
        from decimal import Decimal
        from ton_nft_inventory import normalise_ton_address
        from marketplace_payments import PaymentConflict, append_escrow_event, post_ledger_transaction, record_payment_event

        admin = _admin_user(x_telegram_init_data)
        admin_id = str(admin["id"])
        _enforce_user_rate_limit(admin_id, "ton_seller_payout_execution", 5)
        configured_admins = {
            part.strip() for part in os.getenv("ADMIN_TELEGRAM_IDS", os.getenv("ADMIN_TELEGRAM_ID", "")).split(",")
            if part.strip().isdigit()
        }
        if len(configured_admins) < 2:
            raise HTTPException(status_code=503, detail="Two distinct dispute administrators must be configured")
        if (
            os.getenv("HASSLE_TON_CUSTODY_ACKNOWLEDGED", "0") != "1"
            or os.getenv("HASSLE_TON_PAYOUT_MODE", "") != "manual_multisig_2_of_3"
        ):
            raise HTTPException(status_code=503, detail="Manual 2-of-3 TON multisig custody policy must be acknowledged")
        token = os.getenv("TONAPI_TOKEN", "").strip()
        treasury = os.getenv("HASSLE_TON_TREASURY_ADDRESS", "").strip()
        if not token or not treasury:
            raise HTTPException(status_code=503, detail="TON payout verification or treasury is not configured")
        try:
            treasury_raw = normalise_ton_address(treasury)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Configured TON treasury address is invalid") from exc

        with _db() as conn:
            begin_write(conn, f"deal:{deal_id}")
            deal = conn.execute(
                select_for_update(conn, "SELECT * FROM marketplace_deals WHERE id=?"),
                (deal_id,),
            ).fetchone()
            if deal is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Deal not found")
            prior = conn.execute(
                """SELECT event_data FROM marketplace_escrow_events
                   WHERE deal_id=? AND event_type='payout_released' ORDER BY created_at DESC LIMIT 1""",
                (deal_id,),
            ).fetchone()
            if prior is not None:
                prior_data = json.loads(prior["event_data"] or "{}")
                if prior_data.get("provider_reference") == payload.transaction_hash:
                    conn.rollback()
                    return {"deal_id": deal_id, "payout_status": "paid", "transaction_hash": payload.transaction_hash, "idempotent_replay": True}
                conn.rollback()
                raise HTTPException(status_code=409, detail="A payout has already been recorded for this deal")
            if deal["status"] != "completed" or str(deal["currency"]) != "TON":
                conn.rollback()
                raise HTTPException(status_code=409, detail="Only completed TON deals can be paid out")
            seller_id = str(deal["listing_owner_id"])
            payment_intent = conn.execute(
                """SELECT * FROM marketplace_payment_intents
                   WHERE deal_id=? AND provider='ton' AND status='confirmed'
                   ORDER BY created_at DESC LIMIT 1""",
                (deal_id,),
            ).fetchone()
            if payment_intent is None or not payment_intent["provider_reference"]:
                conn.rollback()
                raise HTTPException(status_code=409, detail="A confirmed TON payment intent is required before seller payout")
            payment_metadata = json.loads(payment_intent["metadata_json"] or "{}")
            try:
                payment_treasury = normalise_ton_address(str(payment_metadata.get("treasury") or ""))
            except Exception as exc:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Original TON payment treasury is missing or invalid") from exc
            if payment_treasury != treasury_raw or Decimal(str(payment_intent["amount"])) <= 0:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Configured payout treasury does not match the original TON payment treasury")
            seller_wallet = conn.execute(
                """SELECT address FROM verified_wallets
                   WHERE telegram_id=? AND network='-239' AND revoked_at IS NULL
                   ORDER BY last_verified_at DESC LIMIT 1""",
                (seller_id,),
            ).fetchone()
            payable = conn.execute(
                """SELECT lt.id, lt.amount
                   FROM marketplace_ledger_transactions lt
                   JOIN marketplace_ledger_entries le ON le.transaction_id=lt.id
                   WHERE lt.reference_id=? AND lt.currency='TON'
                     AND le.account_type='seller_payable' AND le.account_id=?
                     AND le.direction='credit'
                   ORDER BY lt.created_at DESC LIMIT 2""",
                (deal_id, seller_id),
            ).fetchall()
            if seller_wallet is None or len(payable) != 1:
                conn.rollback()
                raise HTTPException(status_code=409, detail="A single recorded TON seller payable and an active verified seller wallet are required")
            payable_amount = str(payable[0]["amount"])
            payment_intent_id = str(payment_intent["id"])
            if Decimal(payable_amount) != Decimal(str(payment_intent["amount"])):
                conn.rollback()
                raise HTTPException(status_code=409, detail="Seller payable does not match the confirmed TON payment amount")
            if conn.execute(
                "SELECT 1 FROM marketplace_ledger_transactions WHERE reference_type='withdrawal' AND reference_id=? LIMIT 1",
                (deal_id,),
            ).fetchone():
                conn.rollback()
                raise HTTPException(status_code=409, detail="A seller payout is already recorded for this deal")
            seller_raw = normalise_ton_address(seller_wallet["address"])
            conn.commit()

        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
                response = await client.get(
                    f"{os.getenv('TONAPI_BASE_URL', 'https://tonapi.io').rstrip('/')}/v2/blockchain/transactions/{payload.transaction_hash}",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
                response.raise_for_status()
                transaction = response.json()
            account_value = transaction.get("account") if isinstance(transaction, dict) else None
            if isinstance(account_value, dict):
                account_value = account_value.get("address")
            if not isinstance(transaction, dict) or transaction.get("success") is not True:
                raise HTTPException(status_code=409, detail="TON has not confirmed a successful seller payout")
            if normalise_ton_address(str(account_value or "")) != treasury_raw:
                raise HTTPException(status_code=409, detail="Payout transaction was not sent by the configured treasury")
            expected_nano = int(Decimal(payable_amount) * Decimal(1_000_000_000))
            paid = False
            for message in transaction.get("out_msgs", []) if isinstance(transaction.get("out_msgs"), list) else []:
                if not isinstance(message, dict) or message.get("bounced") is True:
                    continue
                try:
                    destination = normalise_ton_address(str(message.get("destination") or ""))
                    value = int(message.get("value") or 0)
                except Exception:
                    continue
                if destination == seller_raw and value == expected_nano:
                    paid = True
                    break
            if not paid:
                raise HTTPException(status_code=409, detail="TON transaction does not contain the exact payout to the verified seller wallet")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="TON seller payout transaction could not be verified") from exc

        now = int(time.time())
        with _db() as conn:
            begin_write(conn, f"deal:{deal_id}")
            locked = conn.execute(
                select_for_update(conn, "SELECT * FROM marketplace_deals WHERE id=?"),
                (deal_id,),
            ).fetchone()
            if locked is None or locked["status"] != "completed":
                conn.rollback()
                raise HTTPException(status_code=409, detail="Deal state changed during payout verification")
            prior = conn.execute(
                """SELECT event_data FROM marketplace_escrow_events
                   WHERE deal_id=? AND event_type='payout_released' LIMIT 1""",
                (deal_id,),
            ).fetchone()
            if prior is not None:
                prior_data = json.loads(prior["event_data"] or "{}")
                if prior_data.get("provider_reference") == payload.transaction_hash:
                    conn.rollback()
                    return {"deal_id": deal_id, "payout_status": "paid", "transaction_hash": payload.transaction_hash, "idempotent_replay": True}
                conn.rollback()
                raise HTTPException(status_code=409, detail="A different payout was recorded while verification was running")
            try:
                payout_event_hash = hashlib.sha256(
                    f"{deal_id}:{payload.transaction_hash}:{seller_raw}:{expected_nano}".encode("utf-8")
                ).hexdigest()
                record_payment_event(
                    conn,
                    provider="ton",
                    provider_event_id=payload.transaction_hash,
                    payment_intent_id=payment_intent_id,
                    event_type="seller_payout_confirmed",
                    payload_hash=payout_event_hash,
                    now=now,
                )
                ledger = post_ledger_transaction(
                    conn,
                    idempotency_key=f"ton-seller-payout:{payload.transaction_hash}",
                    reference_type="withdrawal",
                    reference_id=deal_id,
                    currency="TON",
                    debit_account=("seller_payable", str(locked["listing_owner_id"])),
                    credit_account=("ton_treasury_clearing", treasury_raw),
                    debit_amount=payable_amount,
                    credit_amount=payable_amount,
                    now=now,
                )
                append_escrow_event(
                    conn,
                    deal_id=deal_id,
                    actor_user_id=admin_id,
                    event_type="payout_released",
                    event_key=f"{deal_id}:ton-payout:{payload.transaction_hash}",
                    event_data={
                        "provider": "ton",
                        "provider_reference": payload.transaction_hash,
                        "seller_telegram_id": str(locked["listing_owner_id"]),
                        "seller_wallet": seller_raw,
                        "ledger_transaction_id": ledger["id"],
                        "currency": "TON",
                        "amount": payable_amount,
                        "settlement_status": "paid",
                        "custody_mode": "manual_multisig_2_of_3",
                    },
                    now=now,
                )
            except PaymentConflict as exc:
                conn.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            conn.commit()
        return {
            "deal_id": deal_id,
            "payout_status": "paid",
            "currency": "TON",
            "amount": payable_amount,
            "transaction_hash": payload.transaction_hash,
            "idempotent_replay": False,
        }


    @router.post("/deals/{deal_id}/telegram-business-gift-delivery/transfer")
    async def transfer_telegram_business_gift_delivery(
        deal_id: str,
        payload: TelegramBusinessGiftDeliveryRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Transfer a paid Business-owned gift once, then atomically finalize marketplace ownership."""
        import hashlib
        from telegram_business_gift_inventory import (
            fetch_business_account_gifts,
            transfer_business_account_gift,
            TelegramBusinessGiftInventoryError,
        )
        from marketplace import get_telegram_business_connection, get_telegram_business_gift_ref
        from marketplace_payments import (
            PaymentConflict, PaymentStateError, append_escrow_event,
            post_ledger_transaction, transition_deal_status,
        )

        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        seller_id = str(user["id"])
        _enforce_user_rate_limit(seller_id, "telegram_business_gift_delivery", 4)
        request_digest = hashlib.sha256(payload.idempotency_key.encode("utf-8")).hexdigest()[:24]
        attempt_key = f"{deal_id}:tg-gift-attempt:{request_digest}"
        provider_confirmation_key = f"{deal_id}:tg-gift-provider-ok:{request_digest}"
        completion_key = f"{deal_id}:tg-gift-complete:{request_digest}"
        now = int(time.time())

        # A previously confirmed Telegram response can safely resume database finalization
        # without invoking transferGift again. An attempt without that confirmation is
        # deliberately locked for reconciliation because Telegram transfers are irreversible.
        with _db() as conn:
            deal = conn.execute(
                """SELECT d.*, o.asset_id, a.source, a.external_asset_id, a.owner_telegram_id,
                          a.title, a.asset_type, a.image_url, a.status AS asset_status
                   FROM marketplace_deals d
                   JOIN marketplace_orders o ON o.id=d.order_id
                   JOIN verified_assets a ON a.id=o.asset_id
                   WHERE d.id=?""",
                (deal_id,),
            ).fetchone()
            if deal is None or str(deal["listing_owner_id"]) != seller_id:
                raise HTTPException(status_code=404, detail="Deal not found")
            if deal["source"] != "telegram_gift" or deal["asset_status"] != "verified":
                raise HTTPException(status_code=409, detail="This deal does not contain a verified Telegram Business gift")
            if deal["status"] not in {"paid", "delivery_pending", "completed"}:
                raise HTTPException(status_code=409, detail="The deal must be paid before gift delivery")
            buyer_id = str(deal["offer_sender_id"])
            existing_events = conn.execute(
                """SELECT event_type, event_key, event_data FROM marketplace_escrow_events
                   WHERE deal_id=? AND event_type IN (
                       'asset_transfer_submitted','asset_transfer_rejected','asset_transfer_confirmed'
                   )
                   ORDER BY created_at ASC, id ASC""",
                (deal_id,),
            ).fetchall()
            submitted_attempts: set[str] = set()
            rejected_attempts: set[str] = set()
            provider_confirmed = False
            confirmed_event_key = ""
            for event in existing_events:
                try:
                    event_data = json.loads(event["event_data"] or "{}")
                except (TypeError, ValueError):
                    event_data = {}
                if event_data.get("provider") != "telegram_business":
                    continue
                attempt_digest = str(event_data.get("attempt_digest") or "")
                if event["event_type"] == "asset_transfer_submitted" and attempt_digest:
                    submitted_attempts.add(attempt_digest)
                elif event["event_type"] == "asset_transfer_rejected" and attempt_digest:
                    rejected_attempts.add(attempt_digest)
                if (
                    event["event_type"] == "asset_transfer_confirmed"
                    and str(event_data.get("buyer_telegram_id") or "") == buyer_id
                ):
                    provider_confirmed = True
                    confirmed_event_key = str(event["event_key"])
            pending_attempts = submitted_attempts - rejected_attempts
            if request_digest in rejected_attempts:
                raise HTTPException(
                    status_code=409,
                    detail="This idempotency key already received a definitive Telegram rejection; retry with a new key",
                )
            attempt_exists = bool(pending_attempts)
            if provider_confirmed and confirmed_event_key:
                # A provider-confirmed transfer can resume finalization even if the
                # seller reopens the Mini App and supplies a fresh request key.
                confirmed_digest = confirmed_event_key.rsplit(":tg-gift-provider-ok:", 1)[-1]
                completion_key = f"{deal_id}:tg-gift-complete:{confirmed_digest}"
            if deal["status"] == "completed":
                conn.rollback()
                return {"deal_id": deal_id, "status": "completed", "idempotent_replay": True}
            if attempt_exists and not provider_confirmed:
                raise HTTPException(
                    status_code=409,
                    detail="A Telegram gift transfer was already attempted; automatic retry is blocked pending reconciliation",
                )
            if str(deal["owner_telegram_id"]) != seller_id:
                raise HTTPException(status_code=409, detail="The seller is no longer the recorded gift owner")
            payment = conn.execute(
                """SELECT id FROM marketplace_payment_intents
                   WHERE deal_id=? AND status='confirmed' LIMIT 1""",
                (deal_id,),
            ).fetchone()
            if payment is None:
                raise HTTPException(status_code=409, detail="A confirmed payment is required before gift delivery")
            asset_id = str(deal["asset_id"])
            if not buyer_id.isdigit():
                raise HTTPException(status_code=409, detail="The buyer Telegram account is invalid")
            asset_snapshot = dict(deal)

        if not provider_confirmed:
            connection = get_telegram_business_connection(seller_id)
            gift_ref = get_telegram_business_gift_ref(asset_id, seller_id)
            if not connection or not connection.get("connected") or not connection.get("can_transfer_and_upgrade_gifts") or not gift_ref:
                raise HTTPException(status_code=409, detail="Seller must reconnect Telegram Business and grant gift-transfer permission")
            try:
                inventory = await fetch_business_account_gifts(str(gift_ref["business_connection_id"]))
            except TelegramBusinessGiftInventoryError as exc:
                raise HTTPException(status_code=503, detail="Could not refresh the seller's Telegram gift inventory") from exc
            if not inventory.get("complete"):
                raise HTTPException(status_code=503, detail="Telegram gift inventory is incomplete; transfer is blocked")
            owned_gift_id = str(gift_ref["owned_gift_id"])
            live_gift = next(
                (item for item in inventory["items"] if str(item.get("owned_gift_id")) == owned_gift_id),
                None,
            )
            if live_gift is None or live_gift.get("can_be_transferred") is not True:
                raise HTTPException(status_code=409, detail="Telegram no longer marks this gift as transferable")
            next_transfer = live_gift.get("next_transfer_date")
            if isinstance(next_transfer, int) and next_transfer > now:
                raise HTTPException(status_code=409, detail="Telegram transfer lock has not expired")
            transfer_fee = live_gift.get("transfer_star_count")
            transfer_fee = transfer_fee if isinstance(transfer_fee, int) and not isinstance(transfer_fee, bool) else 0
            if transfer_fee > 0 and not payload.accept_transfer_fee:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "seller_fee_consent_required", "transfer_star_count": transfer_fee},
                )
            if transfer_fee > 0 and not connection.get("can_transfer_stars"):
                raise HTTPException(status_code=409, detail="Telegram Business has no permission to pay the transfer fee")
            # Persist the irreversible-attempt marker before calling Telegram. This is
            # intentionally conservative: a timeout is never retried automatically.
            with _db() as conn:
                begin_write(conn, f"deal:{deal_id}", f"asset:{asset_id}")
                locked = conn.execute(
                    select_for_update(conn, "SELECT * FROM marketplace_deals WHERE id=?"),
                    (deal_id,),
                ).fetchone()
                asset = conn.execute(
                    select_for_update(conn, "SELECT owner_telegram_id,status FROM verified_assets WHERE id=?"),
                    (asset_id,),
                ).fetchone()
                if locked is None or locked["status"] not in {"paid", "delivery_pending"} or asset is None:
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="Deal or gift state changed before transfer")
                if str(locked["listing_owner_id"]) != seller_id or str(asset["owner_telegram_id"]) != seller_id or asset["status"] != "verified":
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="Seller gift ownership changed before transfer")
                previous = conn.execute(
                    """SELECT event_type, event_data FROM marketplace_escrow_events
                       WHERE deal_id=? AND event_type IN ('asset_transfer_submitted','asset_transfer_rejected')
                       ORDER BY created_at ASC, id ASC""",
                    (deal_id,),
                ).fetchall()
                submitted_digests: set[str] = set()
                rejected_digests: set[str] = set()
                for row in previous:
                    previous_data = json.loads(row["event_data"] or "{}")
                    if previous_data.get("provider") != "telegram_business":
                        continue
                    previous_digest = str(previous_data.get("attempt_digest") or "")
                    if row["event_type"] == "asset_transfer_submitted" and previous_digest:
                        submitted_digests.add(previous_digest)
                    elif row["event_type"] == "asset_transfer_rejected" and previous_digest:
                        rejected_digests.add(previous_digest)
                pending_digests = submitted_digests - rejected_digests
                if request_digest in rejected_digests:
                    conn.rollback()
                    raise HTTPException(
                        status_code=409,
                        detail="Use a new idempotency key after a definitive Telegram rejection",
                    )
                if pending_digests:
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="A Telegram gift transfer attempt is awaiting provider reconciliation")
                try:
                    if locked["status"] == "paid":
                        transition_deal_status(
                            conn, deal_id=deal_id, new_status="delivery_pending",
                            actor_user_id=seller_id,
                            idempotency_key=f"{deal_id}:tg-gift-request:{request_digest}",
                            event_type="asset_transfer_requested",
                            event_data={"asset_id": asset_id, "provider": "telegram_business"},
                            now=now,
                        )
                    append_escrow_event(
                        conn, deal_id=deal_id, actor_user_id=seller_id,
                        event_type="asset_transfer_submitted", event_key=attempt_key,
                        event_data={
                            "asset_id": asset_id,
                            "provider": "telegram_business",
                            "transfer_fee_stars": transfer_fee,
                            "seller_fee_consent": bool(payload.accept_transfer_fee),
                            "buyer_telegram_id": buyer_id,
                            "attempt_digest": request_digest,
                        },
                        now=now,
                    )
                except (PaymentConflict, PaymentStateError, ValueError) as exc:
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="Could not safely reserve the Telegram gift transfer") from exc
                conn.commit()
            try:
                await transfer_business_account_gift(
                    str(gift_ref["business_connection_id"]),
                    owned_gift_id,
                    buyer_id,
                    star_count=transfer_fee,
                )
            except TelegramBusinessGiftInventoryError as exc:
                if exc.definitive_rejection:
                    # A definitive Bot API rejection means no transfer occurred.
                    # Record it durably so the seller can correct permissions/eligibility
                    # and retry with a new idempotency key without risking a duplicate.
                    with _db() as conn:
                        begin_write(conn, f"deal:{deal_id}")
                        try:
                            append_escrow_event(
                                conn,
                                deal_id=deal_id,
                                actor_user_id=seller_id,
                                event_type="asset_transfer_rejected",
                                event_key=f"{deal_id}:tg-gift-rejected:{request_digest}",
                                event_data={
                                    "asset_id": asset_id,
                                    "provider": "telegram_business",
                                    "attempt_digest": request_digest,
                                    "buyer_telegram_id": buyer_id,
                                    "reason": "telegram_definitive_rejection",
                                },
                                now=int(time.time()),
                            )
                        except (PaymentConflict, PaymentStateError, ValueError) as journal_exc:
                            conn.rollback()
                            raise HTTPException(
                                status_code=503,
                                detail="Telegram rejected the transfer, but the rejection journal needs reconciliation",
                            ) from journal_exc
                        conn.commit()
                    raise HTTPException(
                        status_code=409,
                        detail="Telegram rejected the gift transfer; correct the Business permissions or gift eligibility and retry",
                    ) from exc
                # A network timeout or server error may happen after Telegram accepted
                # the transfer. Keep this attempt locked pending manual reconciliation.
                raise HTTPException(
                    status_code=502,
                    detail="Telegram transfer result is unconfirmed; automatic retry is blocked for safety",
                ) from exc
            with _db() as conn:
                begin_write(conn, f"deal:{deal_id}")
                try:
                    append_escrow_event(
                        conn, deal_id=deal_id, actor_user_id=seller_id,
                        event_type="asset_transfer_confirmed",
                        event_key=provider_confirmation_key,
                        event_data={
                            "asset_id": asset_id,
                            "provider": "telegram_business",
                            "provider_confirmation": True,
                            "buyer_telegram_id": buyer_id,
                            "transfer_fee_stars": transfer_fee,
                        },
                        now=int(time.time()),
                    )
                except (PaymentConflict, PaymentStateError, ValueError) as exc:
                    conn.rollback()
                    raise HTTPException(
                        status_code=503,
                        detail="Telegram confirmed the transfer, but the confirmation journal needs reconciliation",
                    ) from exc
                conn.commit()
                confirmed_event_key = provider_confirmation_key

        # Finalize from the durable provider confirmation. This section is safe to
        # retry and never calls Telegram a second time.
        with _db() as conn:
            begin_write(conn, f"deal:{deal_id}", f"asset:{asset_id}")
            locked = conn.execute(
                select_for_update(conn, "SELECT * FROM marketplace_deals WHERE id=?"),
                (deal_id,),
            ).fetchone()
            asset = conn.execute(
                select_for_update(conn, "SELECT * FROM verified_assets WHERE id=?"),
                (asset_id,),
            ).fetchone()
            confirmed = conn.execute(
                "SELECT 1 FROM marketplace_escrow_events WHERE event_key=?",
                (confirmed_event_key,),
            ).fetchone()
            if confirmed is None:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Telegram transfer confirmation is not recorded")
            if locked is None or asset is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Deal or gift record not found")
            if locked["status"] == "completed" and str(asset["owner_telegram_id"]) == buyer_id:
                conn.rollback()
                return {"deal_id": deal_id, "status": "completed", "idempotent_replay": True}
            if locked["status"] != "delivery_pending" or str(asset["owner_telegram_id"]) != seller_id or asset["status"] != "verified":
                conn.rollback()
                raise HTTPException(status_code=409, detail="Gift ownership or deal state changed after Telegram transfer")
            verification_reference = hashlib.sha256(
                f"hassle-telegram-business-transfer:{deal_id}:{asset_id}:{buyer_id}:{request_digest}".encode("utf-8")
            ).hexdigest()
            try:
                conn.execute(
                    """INSERT INTO asset_owner_transfer_authorizations
                       (asset_id, previous_owner_id, new_owner_id, verification_reference_hash, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (asset_id, seller_id, buyer_id, verification_reference, int(time.time())),
                )
                changed = conn.execute(
                    """UPDATE verified_assets
                       SET owner_telegram_id=?, verification_method='telegram_gift_owner',
                           verification_reference_hash=?, verified_at=?, status='verified', revoked_at=NULL
                       WHERE id=? AND owner_telegram_id=? AND status='verified'""",
                    (buyer_id, verification_reference, int(time.time()), asset_id, seller_id),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("Telegram gift ownership update did not match")
                conn.execute(
                    """INSERT INTO asset_ownership_history
                       (asset_id, previous_owner_id, new_owner_id, verification_reference_hash, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (asset_id, seller_id, buyer_id, verification_reference, int(time.time())),
                )
                conn.execute("DELETE FROM telegram_business_gift_refs WHERE asset_id=?", (asset_id,))
                transition_deal_status(
                    conn, deal_id=deal_id, new_status="completed",
                    actor_user_id=seller_id, idempotency_key=completion_key,
                    event_type="deal_completed",
                    event_data={
                        "asset_id": asset_id,
                        "provider": "telegram_business",
                        "new_owner_telegram_id": buyer_id,
                        "verification": "telegram_transferGift_success",
                    },
                    now=int(time.time()),
                )
                seller_credit = post_ledger_transaction(
                    conn,
                    idempotency_key=f"{deal_id}:seller-payable:{locked['currency']}",
                    reference_type="deal", reference_id=deal_id,
                    currency=str(locked["currency"]),
                    debit_account=("escrow", deal_id),
                    credit_account=("seller_payable", seller_id),
                    debit_amount=locked["agreed_amount"],
                    credit_amount=locked["agreed_amount"],
                    now=int(time.time()),
                )
                append_escrow_event(
                    conn, deal_id=deal_id, actor_user_id=seller_id,
                    event_type="seller_credit_recorded",
                    event_key=f"{deal_id}:seller-credit-recorded",
                    event_data={
                        "ledger_transaction_id": seller_credit["id"],
                        "seller_telegram_id": seller_id,
                        "currency": str(locked["currency"]),
                        "amount": str(locked["agreed_amount"]),
                        "settlement_status": "pending_manual_payout",
                    },
                    now=int(time.time()),
                )
            except (PaymentConflict, PaymentStateError, ValueError) as exc:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Could not safely finalize Telegram gift delivery") from exc
            except Exception as exc:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Telegram confirmed delivery; ownership finalization requires reconciliation") from exc
            conn.commit()
        return {
            "deal_id": deal_id,
            "status": "completed",
            "asset_id": asset_id,
            "owner_telegram_id": buyer_id,
            "settlement_status": "pending_manual_payout",
            "idempotent_replay": False,
        }


    @router.post("/deals/{deal_id}/ton-nft-delivery/prepare")
    async def prepare_ton_nft_delivery(
        deal_id: str,
        payload: TonNftDeliveryPrepareRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Prepare a seller-signed TEP-62 transfer; the platform never holds wallet keys."""
        import hashlib
        from ton_nft_inventory import build_nft_transfer_payload, fetch_nft_owner, normalise_ton_address

        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        seller_id = str(user["id"])
        _enforce_user_rate_limit(seller_id, "ton_nft_delivery_prepare", 5)
        with _db() as conn:
            deal = conn.execute(
                """SELECT d.*, o.asset_id, o.title AS asset_title,
                          a.source, a.external_asset_id, a.owner_telegram_id,
                          a.asset_type, a.image_url, a.status AS asset_status
                   FROM marketplace_deals d
                   JOIN marketplace_orders o ON o.id = d.order_id
                   JOIN verified_assets a ON a.id = o.asset_id
                   WHERE d.id = ?""",
                (deal_id,),
            ).fetchone()
            if deal is None or str(deal["listing_owner_id"]) != seller_id:
                raise HTTPException(status_code=404, detail="Deal not found")
            if deal["status"] not in {"paid", "delivery_pending"}:
                raise HTTPException(status_code=409, detail="The deal must be paid before NFT delivery")
            if deal["source"] != "ton_nft" or deal["asset_status"] != "verified" or str(deal["owner_telegram_id"]) != seller_id:
                raise HTTPException(status_code=409, detail="This deal does not contain a currently verified TON NFT owned by the seller")
            seller_wallet = conn.execute(
                """SELECT address FROM verified_wallets
                   WHERE telegram_id = ? AND network = '-239' AND revoked_at IS NULL
                   ORDER BY last_verified_at DESC LIMIT 1""",
                (seller_id,),
            ).fetchone()
            buyer_wallet = conn.execute(
                """SELECT address FROM verified_wallets
                   WHERE telegram_id = ? AND network = '-239' AND revoked_at IS NULL
                   ORDER BY last_verified_at DESC LIMIT 1""",
                (str(deal["offer_sender_id"]),),
            ).fetchone()
        if seller_wallet is None or buyer_wallet is None:
            raise HTTPException(status_code=409, detail="Both buyer and seller must link a TON mainnet wallet with TON Proof")
        seller_address = seller_wallet["address"]
        buyer_address = buyer_wallet["address"]
        try:
            current_owner = await fetch_nft_owner(str(deal["external_asset_id"]))
            seller_raw = normalise_ton_address(seller_address)
            buyer_raw = normalise_ton_address(buyer_address)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="TON ownership verification is temporarily unavailable") from exc
        if current_owner.get("is_scam") or current_owner.get("owner_address") != seller_raw:
            raise HTTPException(status_code=409, detail="The seller no longer owns this NFT on-chain")
        # A deal has exactly one intended NFT handoff. Keep the TEP-62 query_id
        # stable across repeated prepare calls so a fresh HTTP idempotency key cannot
        # produce a second, distinct transfer payload for the same delivery.
        query_id = int(hashlib.sha256(
            f"hassle-ton-nft-delivery:{deal_id}".encode("utf-8")
        ).hexdigest()[:16], 16)
        try:
            transfer_payload = build_nft_transfer_payload(
                new_owner_address=buyer_address,
                response_destination=seller_address,
                query_id=query_id,
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Could not construct the TON NFT transfer request") from exc
        now = int(time.time())
        with _db() as conn:
            begin_write(conn, f"deal:{deal_id}", f"asset:{deal['asset_id']}")
            locked = conn.execute(
                select_for_update(conn, "SELECT status FROM marketplace_deals WHERE id = ?"),
                (deal_id,),
            ).fetchone()
            if locked is None or locked["status"] not in {"paid", "delivery_pending"}:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Deal state changed before delivery preparation")
            from marketplace_payments import PaymentConflict, PaymentStateError, transition_deal_status
            try:
                if locked["status"] == "paid":
                    result = transition_deal_status(
                        conn,
                        deal_id=deal_id,
                        new_status="delivery_pending",
                        actor_user_id=seller_id,
                        idempotency_key=f"{deal_id}:ton-nft-delivery:{payload.idempotency_key}",
                        event_type="asset_transfer_requested",
                        event_data={
                            "asset_id": str(deal["asset_id"]),
                            "asset_address": str(deal["external_asset_id"]),
                            "seller_wallet": seller_raw,
                            "buyer_wallet": buyer_raw,
                            "network": "ton-mainnet",
                        },
                        now=now,
                    )
                else:
                    existing = conn.execute(
                        """SELECT 1 FROM marketplace_escrow_events
                           WHERE deal_id = ? AND event_type = 'asset_transfer_requested'""",
                        (deal_id,),
                    ).fetchone()
                    if existing is None:
                        conn.rollback()
                        raise HTTPException(status_code=409, detail="An NFT delivery request is already active")
                    result = {"idempotent_replay": True}
            except (PaymentConflict, PaymentStateError) as exc:
                conn.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            conn.commit()
        return {
            "deal_id": deal_id,
            "status": "delivery_pending",
            "idempotent_replay": bool(result.get("idempotent_replay")),
            "transaction": {
                "validUntil": now + 600,
                "network": "-239",
                "messages": [{
                    "address": str(deal["external_asset_id"]),
                    "amount": "50000000",
                    "payload": transfer_payload,
                }],
            },
            "seller_wallet": seller_address,
            "buyer_wallet": buyer_address,
            "asset_title": str(deal["asset_title"]),
        }


    @router.post("/deals/{deal_id}/ton-nft-delivery/submitted")
    async def record_ton_nft_delivery_submission(
        deal_id: str,
        payload: TonNftDeliverySubmittedRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Record a wallet-submitted BOC digest without treating it as proof of delivery."""
        import base64
        import hashlib
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        seller_id = str(user["id"])
        _enforce_user_rate_limit(seller_id, "ton_nft_delivery_submitted", 10)
        try:
            raw_boc = base64.b64decode(payload.boc, validate=True)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="TON wallet returned an invalid BOC") from exc
        if not raw_boc or len(raw_boc) > 12000:
            raise HTTPException(status_code=422, detail="TON wallet BOC is outside the allowed size")
        digest = hashlib.sha256(raw_boc).hexdigest()
        with _db() as conn:
            deal = conn.execute(
                "SELECT listing_owner_id, status FROM marketplace_deals WHERE id = ?",
                (deal_id,),
            ).fetchone()
            if deal is None or str(deal["listing_owner_id"]) != seller_id:
                raise HTTPException(status_code=404, detail="Deal not found")
            if deal["status"] != "delivery_pending":
                raise HTTPException(status_code=409, detail="Deal is not awaiting asset delivery")
            begin_write(conn, f"deal:{deal_id}")
            from marketplace_payments import append_escrow_event
            try:
                event = append_escrow_event(
                    conn,
                    deal_id=deal_id,
                    actor_user_id=seller_id,
                    event_type="asset_transfer_submitted",
                    event_key=f"{deal_id}:ton-nft-boc:{digest}",
                    event_data={"wallet_boc_sha256": digest, "network": "ton-mainnet"},
                    now=int(time.time()),
                )
            except Exception as exc:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Could not record the wallet submission") from exc
            conn.commit()
        return {"deal_id": deal_id, "status": "delivery_pending", "submission_recorded": True, "idempotent_replay": event["idempotent_replay"]}


    @router.post("/deals/{deal_id}/ton-nft-delivery/confirm")
    async def confirm_ton_nft_delivery(
        deal_id: str,
        payload: TonNftDeliveryConfirmRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Complete delivery only after TonAPI independently reports the buyer as owner."""
        import hashlib
        from ton_nft_inventory import (
            fetch_nft_owner, fetch_wallet_nft_history,
            find_confirmed_nft_transfer, normalise_ton_address,
        )

        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        buyer_id = str(user["id"])
        _enforce_user_rate_limit(buyer_id, "ton_nft_delivery_confirm", 8)
        with _db() as conn:
            deal = conn.execute(
                """SELECT d.*, o.asset_id, a.source, a.external_asset_id, a.owner_telegram_id,
                          a.title, a.asset_type, a.image_url, a.status AS asset_status,
                          a.verification_reference_hash
                   FROM marketplace_deals d
                   JOIN marketplace_orders o ON o.id = d.order_id
                   JOIN verified_assets a ON a.id = o.asset_id
                   WHERE d.id = ?""",
                (deal_id,),
            ).fetchone()
            if deal is None or str(deal["offer_sender_id"]) != buyer_id:
                raise HTTPException(status_code=404, detail="Deal not found")
            if deal["status"] != "delivery_pending" or deal["source"] != "ton_nft":
                raise HTTPException(status_code=409, detail="This deal is not awaiting TON NFT delivery")
            if deal["asset_status"] != "verified" or str(deal["owner_telegram_id"]) != str(deal["listing_owner_id"]):
                raise HTTPException(status_code=409, detail="Seller asset ownership record is not valid")
            buyer_wallet = conn.execute(
                """SELECT address FROM verified_wallets
                   WHERE telegram_id = ? AND network = '-239' AND revoked_at IS NULL
                   ORDER BY last_verified_at DESC LIMIT 1""",
                (buyer_id,),
            ).fetchone()
            seller_wallet = conn.execute(
                """SELECT address FROM verified_wallets
                   WHERE telegram_id = ? AND network = '-239' AND revoked_at IS NULL
                   ORDER BY last_verified_at DESC LIMIT 1""",
                (str(deal["listing_owner_id"]),),
            ).fetchone()
            transfer_request = conn.execute(
                """SELECT created_at FROM marketplace_escrow_events
                   WHERE deal_id=? AND event_type='asset_transfer_requested'
                   ORDER BY created_at DESC LIMIT 1""",
                (deal_id,),
            ).fetchone()
        if buyer_wallet is None or seller_wallet is None or transfer_request is None:
            raise HTTPException(status_code=409, detail="Both verified TON wallets and a recorded delivery request are required")
        try:
            current_owner = await fetch_nft_owner(str(deal["external_asset_id"]))
            buyer_raw = normalise_ton_address(buyer_wallet["address"])
            seller_raw = normalise_ton_address(seller_wallet["address"])
            history = await fetch_wallet_nft_history(seller_wallet["address"])
            confirmed_transfer = find_confirmed_nft_transfer(
                history,
                item_address=str(deal["external_asset_id"]),
                source_address=seller_raw,
                destination_address=buyer_raw,
                not_before=int(transfer_request["created_at"]),
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail="TON ownership or transfer-history verification is temporarily unavailable") from exc
        if current_owner.get("is_scam") or current_owner.get("owner_address") != buyer_raw:
            raise HTTPException(status_code=409, detail="TON has not yet confirmed the buyer as the NFT owner")
        if confirmed_transfer is None:
            raise HTTPException(status_code=409, detail="TON history does not confirm this NFT transfer from the seller to the buyer after delivery was requested")
        reference = hashlib.sha256(
            f"hassle-ton-delivery:{deal_id}:{deal['asset_id']}:{buyer_id}:{buyer_raw}:{confirmed_transfer['transaction_hash']}".encode("utf-8")
        ).hexdigest()
        now = int(time.time())
        with _db() as conn:
            begin_write(conn, f"deal:{deal_id}", f"asset:{deal['asset_id']}")
            locked = conn.execute(
                select_for_update(conn, "SELECT * FROM marketplace_deals WHERE id = ?"),
                (deal_id,),
            ).fetchone()
            asset = conn.execute(
                select_for_update(conn, "SELECT * FROM verified_assets WHERE id = ?"),
                (deal["asset_id"],),
            ).fetchone()
            if locked is None or locked["status"] != "delivery_pending" or asset is None:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Deal or asset state changed during delivery confirmation")
            if str(asset["owner_telegram_id"]) != str(locked["listing_owner_id"]):
                conn.rollback()
                raise HTTPException(status_code=409, detail="Asset ownership has already changed")
            try:
                conn.execute(
                    """INSERT INTO asset_owner_transfer_authorizations
                       (asset_id, previous_owner_id, new_owner_id, verification_reference_hash, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (deal["asset_id"], str(locked["listing_owner_id"]), buyer_id, reference, now),
                )
                changed = conn.execute(
                    """UPDATE verified_assets
                       SET owner_telegram_id = ?, verification_method = 'ton_connect_proof',
                           verification_reference_hash = ?, verified_at = ?, status = 'verified', revoked_at = NULL
                       WHERE id = ? AND owner_telegram_id = ? AND status = 'verified'""",
                    (buyer_id, reference, now, deal["asset_id"], str(locked["listing_owner_id"])),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("Asset ownership update did not match")
                conn.execute(
                    """INSERT INTO asset_ownership_history
                       (asset_id, previous_owner_id, new_owner_id, verification_reference_hash, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (deal["asset_id"], str(locked["listing_owner_id"]), buyer_id, reference, now),
                )
                from marketplace_payments import PaymentConflict, PaymentStateError, append_escrow_event, post_ledger_transaction, transition_deal_status
                transition_deal_status(
                    conn,
                    deal_id=deal_id,
                    new_status="completed",
                    actor_user_id=buyer_id,
                    idempotency_key=f"{deal_id}:ton-nft-delivery-confirmed:{payload.idempotency_key}",
                    event_type="asset_transfer_confirmed",
                    event_data={
                        "asset_id": str(deal["asset_id"]),
                        "asset_address": str(deal["external_asset_id"]),
                        "new_owner_wallet": buyer_raw,
                        "new_owner_telegram_id": buyer_id,
                        "network": "ton-mainnet",
                        "verification": "tonapi_owner_and_transfer_history",
                        "transfer_transaction_hash": confirmed_transfer["transaction_hash"],
                        "transfer_utime": confirmed_transfer["utime"],
                        "transfer_lt": confirmed_transfer["lt"],
                    },
                    now=now,
                )
                seller_credit = post_ledger_transaction(
                    conn,
                    idempotency_key=f"{deal_id}:seller-payable:{deal['currency']}",
                    reference_type="deal",
                    reference_id=deal_id,
                    currency=str(deal["currency"]),
                    debit_account=("escrow", deal_id),
                    credit_account=("seller_payable", str(locked["listing_owner_id"])),
                    debit_amount=deal["agreed_amount"],
                    credit_amount=deal["agreed_amount"],
                    now=now,
                )
                append_escrow_event(
                    conn,
                    deal_id=deal_id,
                    actor_user_id=buyer_id,
                    event_type="seller_credit_recorded",
                    event_key=f"{deal_id}:seller-credit-recorded",
                    event_data={
                        "ledger_transaction_id": seller_credit["id"],
                        "seller_telegram_id": str(locked["listing_owner_id"]),
                        "currency": str(deal["currency"]),
                        "amount": str(deal["agreed_amount"]),
                        "settlement_status": "pending_manual_payout",
                    },
                    now=now,
                )
            except (PaymentConflict, PaymentStateError) as exc:
                conn.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except Exception as exc:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Could not safely record verified NFT ownership transfer") from exc
            conn.commit()
        return {"deal_id": deal_id, "status": "completed", "asset_id": str(deal["asset_id"]), "owner_telegram_id": buyer_id}


    @router.post("/deals/{deal_id}/ton-checkout")
    async def create_ton_checkout(
        deal_id: str,
        payload: TonCheckoutRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Create a TON Connect transfer request with a unique, verifiable memo."""
        import base64
        import hashlib
        from decimal import Decimal, InvalidOperation
        from ton_nft_inventory import normalise_ton_address
        from marketplace_payments import append_escrow_event, create_payment_intent, transition_deal_status, transition_payment_intent

        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        buyer_id = str(user["id"])
        _enforce_user_rate_limit(buyer_id, "ton_checkout", 5)
        if os.getenv("HASSLE_TON_CHECKOUT_ENABLED", "0") != "1":
            raise HTTPException(status_code=503, detail="TON checkout is not enabled")
        treasury = os.getenv("HASSLE_TON_TREASURY_ADDRESS", "").strip()
        support_contact = os.getenv("PAYMENT_SUPPORT_CONTACT", "").strip()
        if not treasury or not support_contact:
            raise HTTPException(status_code=503, detail="TON treasury or payment support is not configured")
        if (
            os.getenv("HASSLE_TON_CUSTODY_ACKNOWLEDGED", "0") != "1"
            or os.getenv("HASSLE_TON_PAYOUT_MODE", "") != "manual_multisig_2_of_3"
        ):
            raise HTTPException(status_code=503, detail="TON multisig custody and manual payout policy must be explicitly acknowledged")
        try:
            treasury_raw = normalise_ton_address(treasury)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Configured TON treasury address is invalid") from exc
        try:
            ttl = max(300, min(int(os.getenv("HASSLE_TON_INVOICE_TTL_SECONDS", "900")), 3600))
        except ValueError:
            ttl = 900
        now = int(time.time())
        # Re-check the seller's live on-chain ownership before accepting buyer funds.
        from ton_nft_inventory import fetch_nft_owner
        with _db() as preflight_conn:
            preflight = preflight_conn.execute(
                """SELECT d.*, o.currency AS listing_currency, o.status AS listing_status,
                          a.source, a.status AS asset_status, a.owner_telegram_id, a.external_asset_id
                   FROM marketplace_deals d
                   JOIN marketplace_orders o ON o.id=d.order_id
                   JOIN verified_assets a ON a.id=o.asset_id
                   WHERE d.id=?""",
                (deal_id,),
            ).fetchone()
            seller_wallet_preflight = preflight_conn.execute(
                """SELECT address FROM verified_wallets
                   WHERE telegram_id=? AND network='-239' AND revoked_at IS NULL
                   ORDER BY last_verified_at DESC LIMIT 1""",
                (str(preflight["listing_owner_id"]),),
            ).fetchone() if preflight is not None else None
        if preflight is None or buyer_id != str(preflight["offer_sender_id"]):
            raise HTTPException(status_code=404, detail="Deal not found")
        if seller_wallet_preflight is None or preflight["source"] != "ton_nft" or preflight["asset_status"] != "verified":
            raise HTTPException(status_code=409, detail="Seller must link a TON mainnet wallet and own a verified TON NFT")
        try:
            live_owner = await fetch_nft_owner(str(preflight["external_asset_id"]))
            seller_wallet_raw = normalise_ton_address(seller_wallet_preflight["address"])
        except Exception as exc:
            raise HTTPException(status_code=503, detail="TON ownership verification is temporarily unavailable") from exc
        if live_owner.get("is_scam") or live_owner.get("owner_address") != seller_wallet_raw:
            raise HTTPException(status_code=409, detail="The seller no longer owns this NFT on-chain")
        with _db() as conn:
            begin_write(conn, f"deal:{deal_id}")
            deal = conn.execute(
                select_for_update(conn, """SELECT d.*, o.status AS listing_status, o.asset_id,
                    o.currency AS listing_currency, a.source, a.status AS asset_status,
                    a.owner_telegram_id
                    FROM marketplace_deals d
                    JOIN marketplace_orders o ON o.id = d.order_id
                    LEFT JOIN verified_assets a ON a.id = o.asset_id
                    WHERE d.id = ?"""),
                (deal_id,),
            ).fetchone()
            if deal is None or buyer_id != str(deal["offer_sender_id"]):
                conn.rollback()
                raise HTTPException(status_code=404, detail="Deal not found")
            if (
                deal["currency"] != "TON" or deal["listing_currency"] != "TON"
                or deal["listing_status"] != "matched"
                or deal["status"] not in {"payment_not_enabled", "awaiting_payment", "payment_pending"}
                or deal["source"] != "ton_nft" or deal["asset_status"] != "verified"
                or str(deal["owner_telegram_id"]) != str(deal["listing_owner_id"])
            ):
                conn.rollback()
                raise HTTPException(status_code=409, detail="Deal is not eligible for verified TON NFT checkout")
            buyer_wallet = conn.execute(
                """SELECT address FROM verified_wallets
                   WHERE telegram_id = ? AND network = '-239' AND revoked_at IS NULL
                   ORDER BY last_verified_at DESC LIMIT 1""",
                (buyer_id,),
            ).fetchone()
            if buyer_wallet is None:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Link and verify your TON mainnet wallet first")
            try:
                amount = Decimal(str(deal["agreed_amount"]))
                nano_amount = amount * Decimal(1_000_000_000)
                if not amount.is_finite() or amount <= 0 or nano_amount != nano_amount.to_integral_value():
                    raise ValueError("TON amount must be positive and use no more than 9 decimal places")
            except (InvalidOperation, ValueError) as exc:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Deal has an invalid TON amount") from exc
            buyer_raw = normalise_ton_address(buyer_wallet["address"])
            memo = "HASSLE:" + hashlib.sha256(
                f"{deal_id}:{payload.idempotency_key}:{buyer_raw}".encode("utf-8")
            ).hexdigest()[:40]
            if deal["status"] == "payment_not_enabled":
                transition_deal_status(
                    conn, deal_id=deal_id, new_status="awaiting_payment", actor_user_id=buyer_id,
                    idempotency_key=f"{deal_id}:ton-payment-requested:{payload.idempotency_key}",
                    event_type="deal_payment_requested",
                    event_data={"provider": "ton", "currency": "TON"},
                    now=now,
                )
            intent = conn.execute(
                """SELECT * FROM marketplace_payment_intents
                   WHERE deal_id = ? AND status IN ('created', 'pending')
                   ORDER BY created_at DESC LIMIT 1""",
                (deal_id,),
            ).fetchone()
            if intent is not None and int(intent["expires_at"]) <= now:
                transition_payment_intent(conn, intent["id"], "expired", now=now)
                intent = None
                if deal["status"] == "payment_pending":
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="The previous TON invoice expired; this reservation must be released or expire before a new checkout")
            if intent is not None:
                metadata = json.loads(intent["metadata_json"] or "{}")
                memo = str(metadata.get("memo") or memo)
                if (
                    intent["provider"] != "ton" or intent["currency"] != "TON"
                    or Decimal(str(intent["amount"])) != amount
                    or str(metadata.get("buyer_wallet")) != buyer_raw
                    or str(metadata.get("treasury")) != treasury_raw
                ):
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="An incompatible payment request is already active")
                intent = dict(intent)
            else:
                intent = create_payment_intent(
                    conn, deal_id=deal_id, provider="ton", currency="TON", amount=str(amount),
                    idempotency_key=f"ton:{deal_id}:{payload.idempotency_key}",
                    expires_at=now + ttl, invoice_payload=memo,
                    metadata={
                        "buyer_telegram_id": buyer_id, "seller_telegram_id": str(deal["listing_owner_id"]),
                        "buyer_wallet": buyer_raw, "treasury": treasury_raw, "memo": memo,
                    }, now=now,
                )
            if intent["status"] == "created":
                transition_payment_intent(conn, intent["id"], "pending", now=now)
            if deal["status"] != "payment_pending":
                transition_deal_status(
                    conn, deal_id=deal_id, new_status="payment_pending", actor_user_id=buyer_id,
                    idempotency_key=f"{deal_id}:ton-payment-pending:{intent['id']}",
                    event_type="payment_pending",
                    event_data={"provider": "ton", "payment_intent_id": intent["id"], "memo": memo},
                    now=now,
                )
            append_escrow_event(
                conn, deal_id=deal_id, actor_user_id=buyer_id,
                event_type="payment_intent_created",
                event_key=f"{deal_id}:ton-intent:{intent['id']}",
                event_data={"provider": "ton", "payment_intent_id": intent["id"], "currency": "TON",
                            "amount": str(amount), "memo": memo, "treasury": treasury_raw},
                now=now,
            )
            conn.commit()
        # TON Connect comment payload: 32-bit zero op-code followed by UTF-8 memo.
        # Cell BOC is constructed server-side; wallet signs only the transfer.
        try:
            from tonsdk.boc import begin_cell
            comment_boc = begin_cell().store_uint(0, 32).store_string(memo).end_cell().to_boc(False).hex()
            comment_payload = base64.b64encode(bytes.fromhex(comment_boc)).decode("ascii")
        except Exception as exc:
            raise HTTPException(status_code=503, detail="TON transfer payload encoder is unavailable") from exc
        return {
            "deal_id": deal_id, "payment_intent_id": intent["id"], "provider": "ton",
            "currency": "TON", "amount": str(amount), "memo": memo,
            "buyer_wallet": buyer_wallet["address"],
            "expires_at": intent["expires_at"],
            "transaction": {
                "validUntil": min(now + ttl, int(intent["expires_at"])),
                "network": "-239",
                "messages": [{"address": treasury, "amount": str(int(nano_amount)), "payload": comment_payload}],
            },
        }


    @router.post("/deals/{deal_id}/ton-payment/confirm")
    async def confirm_ton_checkout(
        deal_id: str,
        payload: TonPaymentConfirmRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Confirm a TON deposit only from a successful incoming treasury transaction."""
        import hashlib
        import httpx
        from decimal import Decimal
        from ton_nft_inventory import normalise_ton_address
        from marketplace_payments import PaymentConflict, PaymentStateError, append_escrow_event, post_ledger_transaction, record_payment_event, transition_deal_status, transition_payment_intent

        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        buyer_id = str(user["id"])
        _enforce_user_rate_limit(buyer_id, "ton_payment_confirm", 8)
        token = os.getenv("TONAPI_TOKEN", "").strip()
        if not token:
            raise HTTPException(status_code=503, detail="TON transaction verification provider is not configured")
        with _db() as conn:
            intent = conn.execute(
                """SELECT pi.*, d.offer_sender_id, d.listing_owner_id, d.status AS deal_status
                   FROM marketplace_payment_intents pi
                   JOIN marketplace_deals d ON d.id = pi.deal_id
                   WHERE pi.deal_id = ? AND pi.provider = 'ton'
                   ORDER BY pi.created_at DESC LIMIT 1""",
                (deal_id,),
            ).fetchone()
            if intent is None or str(intent["offer_sender_id"]) != buyer_id:
                raise HTTPException(status_code=404, detail="Payment request not found")
            if intent["status"] == "confirmed":
                return {"deal_id": deal_id, "status": "paid", "currency": "TON", "idempotent_replay": True}
            if intent["status"] != "pending" or int(intent["expires_at"]) <= int(time.time()):
                raise HTTPException(status_code=409, detail="TON payment request is not active")
            metadata = json.loads(intent["metadata_json"] or "{}")
        treasury = str(metadata["treasury"])
        buyer_wallet = str(metadata["buyer_wallet"])
        memo = str(metadata["memo"])
        try:
            treasury_raw = normalise_ton_address(treasury)
            buyer_raw = normalise_ton_address(buyer_wallet)
            base_url = os.getenv("TONAPI_BASE_URL", "https://tonapi.io").rstrip("/")
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
                response = await client.get(
                    f"{base_url}/v2/blockchain/accounts/{treasury_raw}/transactions?limit=100",
                    headers=headers,
                )
                response.raise_for_status()
                body = response.json()
            transactions = body.get("transactions", []) if isinstance(body, dict) else []
            expected_nano = int(Decimal(str(intent["amount"])) * Decimal(1_000_000_000))
            matched = None
            for tx in transactions if isinstance(transactions, list) else []:
                if not isinstance(tx, dict) or tx.get("success") is not True:
                    continue
                if int(tx.get("utime") or 0) < int(intent["created_at"]) - 60:
                    continue
                incoming = tx.get("in_msg") if isinstance(tx.get("in_msg"), dict) else {}
                try:
                    source = normalise_ton_address(str(incoming.get("source") or ""))
                    destination = normalise_ton_address(str(incoming.get("destination") or ""))
                    value = int(incoming.get("value") or 0)
                except Exception:
                    continue
                content = incoming.get("decoded_body") if isinstance(incoming.get("decoded_body"), dict) else {}
                comment = content.get("text") or content.get("comment")
                if not comment:
                    nested = incoming.get("message_content")
                    if isinstance(nested, dict):
                        decoded = nested.get("decoded")
                        if isinstance(decoded, dict):
                            comment = decoded.get("comment") or decoded.get("text")
                if (
                    source == buyer_raw and destination == treasury_raw
                    and value == expected_nano and str(comment or "") == memo
                ):
                    matched = tx
                    break
            if matched is None:
                raise HTTPException(status_code=409, detail="TON has not confirmed the exact payment yet")
            provider_reference = str(matched.get("hash") or "").strip()
            if not provider_reference:
                raise HTTPException(status_code=503, detail="TON provider returned a transaction without a hash")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="TON transaction verification is temporarily unavailable") from exc
        now = int(time.time())
        with _db() as conn:
            begin_write(conn, f"deal:{deal_id}", f"payment-intent:{intent['id']}")
            locked = conn.execute(
                select_for_update(conn, "SELECT * FROM marketplace_payment_intents WHERE id = ?"),
                (intent["id"],),
            ).fetchone()
            deal = conn.execute(
                select_for_update(conn, "SELECT * FROM marketplace_deals WHERE id = ?"),
                (deal_id,),
            ).fetchone()
            if locked is None or deal is None or locked["status"] != "pending" or deal["status"] != "payment_pending":
                conn.rollback()
                if locked is not None and locked["status"] == "confirmed":
                    return {"deal_id": deal_id, "status": "paid", "currency": "TON", "idempotent_replay": True}
                raise HTTPException(status_code=409, detail="Deal or TON payment state changed during verification")
            event_hash = hashlib.sha256(
                f"{deal_id}:{provider_reference}:{buyer_raw}:{treasury_raw}:{expected_nano}:{memo}".encode()
            ).hexdigest()
            record_payment_event(
                conn, provider="ton", provider_event_id=provider_reference,
                payment_intent_id=intent["id"], event_type="confirmed_incoming_transfer",
                payload_hash=event_hash, now=now,
            )
            transition_payment_intent(conn, intent["id"], "confirmed", provider_reference=provider_reference, now=now)
            transition_deal_status(
                conn, deal_id=deal_id, new_status="paid", actor_user_id=buyer_id,
                idempotency_key=f"{deal_id}:ton-payment-confirmed:{intent['id']}",
                event_type="payment_confirmed",
                event_data={"provider": "ton", "payment_intent_id": intent["id"], "provider_reference": provider_reference,
                            "currency": "TON", "amount": str(intent["amount"])}, now=now,
            )
            ledger = post_ledger_transaction(
                conn, idempotency_key=f"{deal_id}:ton-escrow:{provider_reference}",
                reference_type="deal", reference_id=deal_id, currency="TON",
                debit_account=("ton_treasury_clearing", provider_reference),
                credit_account=("escrow", deal_id), debit_amount=intent["amount"],
                credit_amount=intent["amount"], now=now,
            )
            append_escrow_event(
                conn, deal_id=deal_id, actor_user_id=buyer_id, event_type="escrow_funded",
                event_key=f"{deal_id}:ton-escrow-funded:{intent['id']}",
                event_data={"provider": "ton", "payment_intent_id": intent["id"],
                            "ledger_transaction_id": ledger["id"], "currency": "TON",
                            "amount": str(intent["amount"]), "custody": "configured_ton_treasury"},
                now=now,
            )
            conn.commit()
        return {"deal_id": deal_id, "status": "paid", "currency": "TON", "amount": str(intent["amount"]), "idempotent_replay": False}


    @router.get("/deals/{deal_id}/payment-status")
    async def get_deal_payment_status(
        deal_id: str,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Expose safe payment status metadata to the two deal participants only."""
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        actor_id = str(user["id"])
        _enforce_user_rate_limit(actor_id, "deal_payment_status", 30)
        with _db() as conn:
            deal = conn.execute(
                """SELECT listing_owner_id, offer_sender_id, status, agreed_amount, currency
                   FROM marketplace_deals WHERE id = ?""",
                (deal_id,),
            ).fetchone()
            if deal is None or actor_id not in (deal["listing_owner_id"], deal["offer_sender_id"]):
                raise HTTPException(status_code=404, detail="Deal not found")
            rows = conn.execute(
                """SELECT id, provider, currency, amount, status, expires_at,
                          created_at, updated_at, confirmed_at
                   FROM marketplace_payment_intents
                   WHERE deal_id = ? ORDER BY created_at DESC, id DESC""",
                (deal_id,),
            ).fetchall()
        return {
            "deal_id": deal_id,
            "deal_status": deal["status"],
            "agreed_amount": deal["agreed_amount"],
            "currency": deal["currency"],
            "payments_enabled": False,
            "checkout_available": False,
            "intents": [dict(row) for row in rows],
        }


    @router.get("/deals")
    async def list_deals(
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=10000),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """List real reservations for their two participants; never imply payment or delivery."""
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        user_id = str(user["id"])
        _enforce_user_rate_limit(user_id, "list_deals", 30)
        with _db() as conn:
            where = "(d.listing_owner_id = ? OR d.offer_sender_id = ?)"
            values = (user_id, user_id)
            total = conn.execute(
                f"SELECT COUNT(*) FROM marketplace_deals d WHERE {where}", values
            ).fetchone()[0]
            rows = conn.execute(
                f"""SELECT d.*, o.title AS order_title, o.kind AS order_kind,
                           o.status AS order_status, a.source AS asset_source, a.status AS asset_status, f.note AS offer_note
                    FROM marketplace_deals d
                    JOIN marketplace_orders o ON o.id = d.order_id
                    LEFT JOIN verified_assets a ON a.id = o.asset_id
                    JOIN marketplace_offers f ON f.id = d.offer_id
                    WHERE {where}
                    ORDER BY d.created_at DESC, d.id DESC LIMIT ? OFFSET ?""",
                (*values, limit, offset),
            ).fetchall()
        items = [{
            "id": row["id"],
            "order_id": row["order_id"],
            "offer_id": row["offer_id"],
            "listing_owner_id": row["listing_owner_id"],
            "offer_sender_id": row["offer_sender_id"],
            "is_seller": row["listing_owner_id"] == user_id,
            "order_title": row["order_title"],
            "order_kind": row["order_kind"],
            "agreed_amount": row["agreed_amount"],
            "currency": row["currency"],
            "status": row["status"],
            "order_status": row["order_status"],
            "offer_note": row["offer_note"],
            "asset_source": row["asset_source"],
            "created_at": row["created_at"],
            "reservation_active": row["status"] not in {"cancelled", "expired", "completed"},
            "payments_enabled": (
                (
                    row["currency"] == "XTR"
                    and os.getenv("HASSLE_STARS_CHECKOUT_ENABLED", "0") == "1"
                    and os.getenv("HASSLE_STARS_CUSTODY_ACKNOWLEDGED", "0") == "1"
                    and os.getenv("HASSLE_STARS_PAYOUT_MODE", "") == "internal_xtr_credit"
                    and bool(os.getenv("BOT_TOKEN", "").strip())
                    and bool(os.getenv("PAYMENT_SUPPORT_CONTACT", "").strip())
                )
                or (
                    row["currency"] == "TON"
                    and os.getenv("HASSLE_TON_CHECKOUT_ENABLED", "0") == "1"
                    and os.getenv("HASSLE_TON_CUSTODY_ACKNOWLEDGED", "0") == "1"
                    and os.getenv("HASSLE_TON_PAYOUT_MODE", "") == "manual_multisig_2_of_3"
                    and bool(os.getenv("HASSLE_TON_TREASURY_ADDRESS", "").strip())
                    and bool(os.getenv("TONAPI_TOKEN", "").strip())
                    and bool(os.getenv("PAYMENT_SUPPORT_CONTACT", "").strip())
                    and row["asset_source"] == "ton_nft"
                    and row["asset_status"] == "verified"
                )
            ),
            "delivery_completed": row["status"] == "completed",
        } for row in rows]
        return {"items": items, "total": total, "limit": limit, "offset": offset,
                "payments_enabled": False}

    @router.get("/notifications")
    async def list_notifications(
        unread_only: bool = False,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=10000),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        if not _verified_asset_actions_enabled():
            _require_user(x_telegram_init_data, verify_telegram_init_data)
            return {"items": [], "total": 0, "limit": limit, "offset": offset, "unread_count": 0}
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        user_id = str(user["id"])
        _enforce_user_rate_limit(user_id, "list_notifications", 60)
        clause = " AND read_at IS NULL" if unread_only else ""
        with _db() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM marketplace_notifications WHERE user_id = ?{clause}",
                (user_id,),
            ).fetchone()[0]
            rows = conn.execute(
                f"""SELECT id, event_type, offer_id, order_id, order_title,
                           actor_name, created_at, read_at
                    FROM marketplace_notifications WHERE user_id = ?{clause}
                    ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
                (user_id, limit, offset),
            ).fetchall()
            unread = conn.execute(
                "SELECT COUNT(*) FROM marketplace_notifications WHERE user_id = ? AND read_at IS NULL",
                (user_id,),
            ).fetchone()[0]
        return {
            "items": [{
                "id": row["id"], "type": row["event_type"], "offer_id": row["offer_id"],
                "order_id": row["order_id"], "order_title": row["order_title"],
                "actor_name": row["actor_name"], "created_at": row["created_at"],
                "read_at": row["read_at"],
            } for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "unread_count": unread,
        }

    @router.patch("/notifications/{notification_id}/read")
    async def mark_notification_read(
        notification_id: str,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        _enforce_user_rate_limit(str(user["id"]), "mark_notification_read", 60)
        with _db() as conn:
            updated = conn.execute(
                """UPDATE marketplace_notifications SET read_at = ?
                   WHERE id = ? AND user_id = ? AND read_at IS NULL""",
                (int(time.time()), notification_id, str(user["id"])),
            )
            exists = conn.execute(
                "SELECT 1 FROM marketplace_notifications WHERE id = ? AND user_id = ?",
                (notification_id, str(user["id"])),
            ).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"read": True, "changed": updated.rowcount == 1}

    @router.post("/notifications/read-all")
    async def mark_all_notifications_read(
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        _enforce_user_rate_limit(str(user["id"]), "mark_all_notifications_read", 10)
        with _db() as conn:
            updated = conn.execute(
                "UPDATE marketplace_notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL",
                (int(time.time()), str(user["id"])),
            )
        return {"read": True, "updated": updated.rowcount}

    @router.get("/account/stats")
    async def account_stats(x_telegram_init_data: str | None = Header(default=None)):
        if not _verified_asset_actions_enabled():
            _require_user(x_telegram_init_data, verify_telegram_init_data)
            return {"active_orders": 0, "completed_orders": 0, "pending_offers_sent": 0, "pending_offers_received": 0, "deals": 0, "unread_notifications": 0}
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        user_id = str(user["id"])
        _enforce_user_rate_limit(user_id, "account_stats", 30)
        with _db() as conn:
            orders = conn.execute(
                """SELECT
                     SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_orders,
                     0 AS completed_orders
                   FROM marketplace_orders WHERE user_id = ?""",
                (user_id,),
            ).fetchone()
            offers = conn.execute(
                """SELECT
                     SUM(CASE WHEN f.sender_user_id = ? AND f.status = 'pending' THEN 1 ELSE 0 END) AS sent_pending,
                     SUM(CASE WHEN o.user_id = ? AND f.status = 'pending' THEN 1 ELSE 0 END) AS received_pending
                   FROM marketplace_offers f JOIN marketplace_orders o ON o.id = f.order_id
                   WHERE f.sender_user_id = ? OR o.user_id = ?""",
                (user_id, user_id, user_id, user_id),
            ).fetchone()
            deal_totals = conn.execute(
                """SELECT COUNT(*) AS deals,
                          SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed_deals
                   FROM marketplace_deals
                   WHERE listing_owner_id=? OR offer_sender_id=?""",
                (user_id, user_id),
            ).fetchone()
            balance_rows = conn.execute(
                """SELECT currency,
                          SUM(CASE WHEN direction='credit' THEN CAST(amount AS NUMERIC)
                                   ELSE -CAST(amount AS NUMERIC) END) AS balance
                   FROM marketplace_ledger_entries
                   WHERE account_type='seller_payable' AND account_id=?
                   GROUP BY currency ORDER BY currency""",
                (user_id,),
            ).fetchall()
            from decimal import Decimal
            seller_pending_settlements = []
            for row in balance_rows:
                amount = Decimal(str(row["balance"] or 0))
                if amount == 0:
                    continue
                seller_pending_settlements.append({
                    "currency": str(row["currency"]),
                    "amount": format(amount, "f"),
                    "status": "pending_manual_payout" if row["currency"] == "TON" else "internal_xtr_credit",
                    "automatic_withdrawal_available": False,
                })
            unread = conn.execute(
                "SELECT COUNT(*) FROM marketplace_notifications WHERE user_id = ? AND read_at IS NULL",
                (user_id,),
            ).fetchone()[0]
        return {
            "active_orders": orders["active_orders"] or 0,
            "completed_orders": deal_totals["completed_deals"] or 0,
            "pending_offers_sent": offers["sent_pending"] or 0,
            "pending_offers_received": offers["received_pending"] or 0,
            "deals": deal_totals["deals"] or 0,
            "seller_pending_settlements": seller_pending_settlements,
            "unread_notifications": unread,
        }

    @router.get("/activity")
    async def account_activity(
        limit: int = Query(default=30, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=10000),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Unified, account-scoped timeline of order, offer, reservation and deal events."""
        if not _verified_asset_actions_enabled():
            _require_user(x_telegram_init_data, verify_telegram_init_data)
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        user_id = str(user["id"])
        _enforce_user_rate_limit(user_id, "account_activity", 60)
        query = """
            WITH activity AS (
                SELECT
                    'order-' || e.id AS id,
                    CASE e.event_type
                      WHEN 'created' THEN 'order_created'
                      WHEN 'filled' THEN 'order_filled'
                      WHEN 'cancelled' THEN 'order_cancelled'
                    END AS type,
                    o.id AS order_id,
                    o.title AS title,
                    CASE WHEN e.actor_user_id = ? THEN 'You' ELSE o.owner_name END AS actor_name,
                    e.created_at AS created_at,
                    o.price AS amount,
                    o.currency AS currency
                FROM marketplace_order_events e
                JOIN marketplace_orders o ON o.id = e.order_id
                WHERE (o.user_id = ? OR e.actor_user_id = ?) AND e.event_type <> 'legacy_unverified'

                UNION ALL

                SELECT
                    'notice-' || n.id AS id,
                    n.event_type AS type,
                    n.order_id AS order_id,
                    n.order_title AS title,
                    n.actor_name AS actor_name,
                    n.created_at AS created_at,
                    f.amount AS amount,
                    f.currency AS currency
                FROM marketplace_notifications n
                JOIN marketplace_offers f ON f.id = n.offer_id
                WHERE n.user_id = ?

                UNION ALL

                SELECT
                    'sent-offer-' || f.id AS id,
                    'offer_sent' AS type,
                    o.id AS order_id,
                    o.title AS title,
                    'You' AS actor_name,
                    f.created_at AS created_at,
                    f.amount AS amount,
                    f.currency AS currency
                FROM marketplace_offers f
                JOIN marketplace_orders o ON o.id = f.order_id
                WHERE f.sender_user_id = ?

                UNION ALL

                SELECT
                    'revision-' || r.id AS id,
                    'order_updated' AS type,
                    o.id AS order_id,
                    o.title AS title,
                    CASE WHEN r.actor_user_id = ? THEN 'You' ELSE o.owner_name END AS actor_name,
                    r.created_at AS created_at,
                    o.price AS amount,
                    o.currency AS currency
                FROM marketplace_order_revisions r
                JOIN marketplace_orders o ON o.id = r.order_id
                WHERE o.user_id = ? OR r.actor_user_id = ?

                UNION ALL

                SELECT
                    'deal-event-' || e.id AS id,
                    e.event_type AS type,
                    o.id AS order_id,
                    o.title AS title,
                    CASE WHEN e.actor_user_id = ? THEN 'You' ELSE o.owner_name END AS actor_name,
                    e.created_at AS created_at,
                    d.agreed_amount AS amount,
                    d.currency AS currency
                FROM marketplace_deal_events e
                JOIN marketplace_deals d ON d.id = e.deal_id
                JOIN marketplace_orders o ON o.id = d.order_id
                WHERE d.listing_owner_id = ? OR d.offer_sender_id = ?

                UNION ALL

                SELECT
                    'deal-lifecycle-' || e.id AS id,
                    e.event_type AS type,
                    o.id AS order_id,
                    o.title AS title,
                    CASE WHEN e.actor_user_id = ? THEN 'You' ELSE o.owner_name END AS actor_name,
                    e.created_at AS created_at,
                    d.agreed_amount AS amount,
                    d.currency AS currency
                FROM marketplace_deal_lifecycle_events e
                JOIN marketplace_deals d ON d.id = e.deal_id
                JOIN marketplace_orders o ON o.id = d.order_id
                WHERE d.listing_owner_id = ? OR d.offer_sender_id = ?

            )
            SELECT id, type, order_id, title, actor_name, created_at, amount, currency
            FROM activity
            ORDER BY created_at DESC,
                     CASE WHEN type IN ('offer_received', 'offer_accepted', 'offer_declined', 'offer_withdrawn') THEN 0
                          ELSE 2 END,
                     id DESC
            LIMIT ? OFFSET ?
        """
        params = (
            user_id, user_id, user_id, user_id, user_id, user_id, user_id, user_id,
            user_id, user_id, user_id, user_id, user_id, user_id, limit, offset,
        )
        count_query = """
            WITH activity AS (
                SELECT e.id FROM marketplace_order_events e JOIN marketplace_orders o ON o.id=e.order_id
                WHERE (o.user_id=? OR e.actor_user_id=?)
                  AND e.event_type <> 'legacy_unverified'
                UNION ALL
                SELECT n.id FROM marketplace_notifications n WHERE n.user_id=?
                UNION ALL
                SELECT f.id FROM marketplace_offers f WHERE f.sender_user_id=?
                UNION ALL
                SELECT r.id FROM marketplace_order_revisions r JOIN marketplace_orders o ON o.id=r.order_id
                WHERE o.user_id=? OR r.actor_user_id=?
                UNION ALL
                SELECT e.id FROM marketplace_deal_events e
                JOIN marketplace_deals d ON d.id=e.deal_id
                WHERE d.listing_owner_id=? OR d.offer_sender_id=?
                UNION ALL
                SELECT e.id FROM marketplace_deal_lifecycle_events e
                JOIN marketplace_deals d ON d.id=e.deal_id
                WHERE d.listing_owner_id=? OR d.offer_sender_id=?
            )
            SELECT COUNT(*) FROM activity
        """
        with _db() as conn:
            rows = conn.execute(query, params).fetchall()
            total = conn.execute(
                count_query, (
                    user_id, user_id, user_id, user_id, user_id, user_id,
                    user_id, user_id, user_id, user_id,
                )
            ).fetchone()[0]
        return {
            "items": [{
                "id": row["id"], "type": row["type"], "order_id": row["order_id"],
                "title": row["title"], "actor_name": row["actor_name"],
                "created_at": row["created_at"], "amount": row["amount"],
                "currency": row["currency"],
            } for row in rows],
            "total": total, "limit": limit, "offset": offset,
        }

    return router
