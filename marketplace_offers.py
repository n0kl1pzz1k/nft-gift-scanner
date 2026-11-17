"""HASSLE NFT marketplace offers.

Offer acceptance stays disabled until production escrow, verified TON/Stars settlement and NFT transfer are implemented. No simulated deals are created.
"""
from __future__ import annotations

import os
import json
import sqlite3
import time
import uuid
from decimal import Decimal
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Literal

from fastapi import APIRouter, Header, HTTPException, Query

from marketplace import _enforce_user_rate_limit, _verified_asset_actions_enabled, _test_unverified_assets_allowed, _migrate_legacy_marketplace_table_names
from pydantic import BaseModel, Field, field_validator

DB_PATH = os.getenv("HASSLE_NFT_DB_PATH", os.getenv("GIFT_RADAR_DB_PATH", os.getenv("DATABASE_PATH", "./data/gift-radar.sqlite3")))


def _connect() -> sqlite3.Connection:
    # Keep both marketplace subsystems on the same connection policy.
    from marketplace import _connect_database
    return _connect_database(DB_PATH)


@contextmanager
def _db():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def init_marketplace_offers_db() -> None:
    with _db() as conn:
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
                conn.execute("BEGIN IMMEDIATE")
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
    action: Literal["accept", "decline", "withdraw"]


class DealAction(BaseModel):
    action: Literal["cancel"]


def create_marketplace_offers_router(verify_telegram_init_data: Callable[[str], dict]) -> APIRouter:
    router = APIRouter(prefix="/api/marketplace", tags=["marketplace-offers"])

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
            conn.execute("BEGIN IMMEDIATE")
            order = conn.execute("SELECT * FROM marketplace_orders WHERE id = ?", (payload.order_id,)).fetchone()
            if order is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Marketplace listing not found")
            if order["kind"] != "listing":
                conn.rollback()
                raise HTTPException(status_code=409, detail="Offers can only be made on seller listings")
            if not _test_unverified_assets_allowed():
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
                SELECT f.*, o.title AS order_title, o.user_id AS order_owner_id,
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
                SELECT f.*, o.title AS order_title, o.user_id AS order_owner_id,
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
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""
                SELECT f.*, o.user_id AS order_owner_id, o.owner_name AS order_owner_name,
                       o.title AS order_title, o.status AS order_status
                FROM marketplace_offers f JOIN marketplace_orders o ON o.id = f.order_id
                WHERE f.id = ?
            """, (offer_id,)).fetchone()
            if row is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Offer not found")
            if row["status"] != "pending":
                conn.rollback()
                raise HTTPException(status_code=409, detail="This offer is no longer pending")
            action = payload.action
            if action in {"accept", "decline"} and actor_id != row["order_owner_id"]:
                conn.rollback()
                raise HTTPException(status_code=403, detail="Only the order owner can respond to this offer")
            if action == "withdraw" and actor_id != row["sender_user_id"]:
                conn.rollback()
                raise HTTPException(status_code=403, detail="Only the offer author can withdraw it")
            if action == "accept":
                conn.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="Offer acceptance is disabled until TON/Stars escrow and NFT transfer are live.",
                )
            new_status = {"accept": "accepted", "decline": "declined", "withdraw": "withdrawn"}[action]
            conn.execute("UPDATE marketplace_offers SET status=?, updated_at=? WHERE id=? AND status='pending'",
                         (new_status, now, offer_id))
            conn.commit()
            updated = conn.execute("""
                SELECT f.*, o.title AS order_title, o.user_id AS order_owner_id,
                       o.owner_name AS order_owner_name, o.status AS order_status
                FROM marketplace_offers f JOIN marketplace_orders o ON o.id = f.order_id WHERE f.id = ?
            """, (offer_id,)).fetchone()
        return {"item": _offer_dict(updated, actor_id)}

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
            conn.execute("BEGIN IMMEDIATE")
            deal = conn.execute(
                """SELECT d.*, o.status AS order_status
                   FROM marketplace_deals d JOIN marketplace_orders o ON o.id = d.order_id
                   WHERE d.id = ?""",
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

    @router.get("/deals/{deal_id}/events")
    async def list_deal_events(
        deal_id: str,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        _require_user(x_telegram_init_data, verify_telegram_init_data)
        raise HTTPException(status_code=404, detail="Deal not found")

    @router.get("/deals")
    async def list_deals(
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=10000),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        # No deal is presented as real until production TON/Stars settlement,
        # escrow and verified NFT delivery are implemented and independently enabled.
        _require_user(x_telegram_init_data, verify_telegram_init_data)
        return {"items": [], "total": 0, "limit": limit, "offset": offset,
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
        with _db() as conn:
            orders = conn.execute(
                """SELECT
                     SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_orders,
                     SUM(CASE WHEN status = 'matched' THEN 1 ELSE 0 END) AS completed_orders
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
            # Deal totals remain zero until real escrow and delivery are enabled.
            deals = 0
            unread = conn.execute(
                "SELECT COUNT(*) FROM marketplace_notifications WHERE user_id = ? AND read_at IS NULL",
                (user_id,),
            ).fetchone()[0]
        return {
            "active_orders": orders["active_orders"] or 0,
            "completed_orders": orders["completed_orders"] or 0,
            "pending_offers_sent": offers["sent_pending"] or 0,
            "pending_offers_received": offers["received_pending"] or 0,
            "deals": deals,
            "unread_notifications": unread,
                    }

    @router.get("/activity")
    async def account_activity(
        limit: int = Query(default=30, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=10000),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Unified, account-scoped timeline of marketplace order and offer events."""
        if not _verified_asset_actions_enabled():
            _require_user(x_telegram_init_data, verify_telegram_init_data)
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        user_id = str(user["id"])
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

            )
            SELECT id, type, order_id, title, actor_name, created_at, amount, currency
            FROM activity
            ORDER BY created_at DESC,
                     CASE WHEN type IN ('offer_received', 'offer_accepted', 'offer_declined', 'offer_withdrawn') THEN 0
                          WHEN type = 'marketplace_deal_created' THEN 1 ELSE 2 END,
                     id DESC
            LIMIT ? OFFSET ?
        """
        params = (user_id, user_id, user_id, user_id, user_id, user_id, user_id, user_id, limit, offset)
        count_query = """
            WITH activity AS (
                SELECT e.id FROM marketplace_order_events e JOIN marketplace_orders o ON o.id=e.order_id
                WHERE o.user_id=? OR e.actor_user_id=?
                UNION ALL
                SELECT n.id FROM marketplace_notifications n WHERE n.user_id=?
                UNION ALL
                SELECT f.id FROM marketplace_offers f WHERE f.sender_user_id=?
                UNION ALL
                SELECT r.id FROM marketplace_order_revisions r JOIN marketplace_orders o ON o.id=r.order_id
                WHERE o.user_id=? OR r.actor_user_id=?
            )
            SELECT COUNT(*) FROM activity
        """
        with _db() as conn:
            rows = conn.execute(query, params).fetchall()
            total = conn.execute(
                count_query, (user_id, user_id, user_id, user_id, user_id, user_id)
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
