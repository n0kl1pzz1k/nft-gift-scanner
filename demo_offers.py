"""Simulation-only offers and deal history for GIFT RADAR.

Offers never trigger payments, wallet actions, asset transfers or blockchain calls.
"""
from __future__ import annotations

import os
import sqlite3
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Callable, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

DB_PATH = os.getenv("GIFT_RADAR_DB_PATH", os.getenv("DATABASE_PATH", "./data/gift-radar.sqlite3"))


def _connect() -> sqlite3.Connection:
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def init_demo_offers_db() -> None:
    with _connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS demo_offers (
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
            FOREIGN KEY(order_id) REFERENCES demo_orders(id)
        );
        CREATE INDEX IF NOT EXISTS idx_demo_offers_order ON demo_offers(order_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_demo_offers_sender ON demo_offers(sender_user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_demo_offers_status ON demo_offers(status, created_at DESC);
        CREATE TABLE IF NOT EXISTS demo_deals (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL UNIQUE,
            offer_id TEXT NOT NULL UNIQUE,
            listing_owner_id TEXT NOT NULL,
            offer_sender_id TEXT NOT NULL,
            agreed_amount TEXT NOT NULL,
            currency TEXT NOT NULL CHECK(currency IN ('TON', 'XTR')),
            created_at INTEGER NOT NULL,
            FOREIGN KEY(order_id) REFERENCES demo_orders(id),
            FOREIGN KEY(offer_id) REFERENCES demo_offers(id)
        );
        """)


def _require_user(init_data: str | None, verify: Callable[[str], dict]) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="Open GIFT RADAR from Telegram to manage demo offers")
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


def create_demo_offers_router(verify_telegram_init_data: Callable[[str], dict]) -> APIRouter:
    router = APIRouter(prefix="/api/marketplace", tags=["demo-offers"])

    @router.post("/offers", status_code=201)
    async def create_offer(payload: CreateOffer, x_telegram_init_data: str | None = Header(default=None)):
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        actor_id = str(user["id"])
        now = int(time.time())
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            order = conn.execute("SELECT * FROM demo_orders WHERE id = ?", (payload.order_id,)).fetchone()
            if order is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Demo order not found")
            if order["status"] != "active":
                conn.rollback()
                raise HTTPException(status_code=409, detail="This demo order is no longer active")
            if order["user_id"] == actor_id:
                conn.rollback()
                raise HTTPException(status_code=403, detail="You cannot make an offer on your own order")
            pending = conn.execute(
                "SELECT COUNT(*) FROM demo_offers WHERE order_id = ? AND sender_user_id = ? AND status = 'pending'",
                (payload.order_id, actor_id),
            ).fetchone()[0]
            if pending:
                conn.rollback()
                raise HTTPException(status_code=409, detail="You already have a pending offer for this order")
            total = conn.execute("SELECT COUNT(*) FROM demo_offers WHERE order_id = ?", (payload.order_id,)).fetchone()[0]
            if total >= 100:
                conn.rollback()
                raise HTTPException(status_code=409, detail="This order has reached its demo offer limit")
            offer_id = "of-" + uuid.uuid4().hex[:16]
            sender_name = (user.get("first_name") or user.get("username") or "Telegram user")[:64]
            conn.execute("""
                INSERT INTO demo_offers
                (id, order_id, sender_user_id, sender_name, amount, currency, note, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """, (offer_id, payload.order_id, actor_id, sender_name, format(payload.amount, "f"),
                  order["currency"], payload.note, now, now))
            conn.commit()
            row = conn.execute("""
                SELECT f.*, o.title AS order_title, o.user_id AS order_owner_id,
                       o.owner_name AS order_owner_name, o.status AS order_status
                FROM demo_offers f JOIN demo_orders o ON o.id = f.order_id WHERE f.id = ?
            """, (offer_id,)).fetchone()
        return {"item": _offer_dict(row, actor_id), "simulation_only": True}

    @router.get("/offers")
    async def list_offers(
        status: str = Query(default="all", pattern="^(pending|accepted|declined|withdrawn|all)$"),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        user_id = str(user["id"])
        where = "f.sender_user_id = ? OR o.user_id = ?"
        values: list[object] = [user_id, user_id]
        if status != "all":
            where = f"({where}) AND f.status = ?"
            values.append(status)
        with _connect() as conn:
            rows = conn.execute(f"""
                SELECT f.*, o.title AS order_title, o.user_id AS order_owner_id,
                       o.owner_name AS order_owner_name, o.status AS order_status
                FROM demo_offers f JOIN demo_orders o ON o.id = f.order_id
                WHERE {where} ORDER BY f.created_at DESC LIMIT 200
            """, values).fetchall()
        return {"items": [_offer_dict(row, user_id) for row in rows]}

    @router.patch("/offers/{offer_id}")
    async def update_offer(offer_id: str, payload: OfferAction,
                           x_telegram_init_data: str | None = Header(default=None)):
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        actor_id = str(user["id"])
        now = int(time.time())
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""
                SELECT f.*, o.user_id AS order_owner_id, o.owner_name AS order_owner_name,
                       o.title AS order_title, o.status AS order_status
                FROM demo_offers f JOIN demo_orders o ON o.id = f.order_id
                WHERE f.id = ?
            """, (offer_id,)).fetchone()
            if row is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Demo offer not found")
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
                if row["order_status"] != "active":
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="The demo order is no longer active")
                conn.execute(
                    "UPDATE demo_orders SET status='matched', updated_at=? WHERE id=? AND status='active'",
                    (now, row["order_id"]),
                )
                conn.execute(
                    "UPDATE demo_offers SET status='declined', updated_at=? WHERE order_id=? AND id<>? AND status='pending'",
                    (now, row["order_id"], offer_id),
                )
                conn.execute("""
                    INSERT INTO demo_deals
                    (id, order_id, offer_id, listing_owner_id, offer_sender_id, agreed_amount, currency, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, ("deal-" + uuid.uuid4().hex[:16], row["order_id"], offer_id,
                      actor_id, row["sender_user_id"], row["amount"], row["currency"], now))
            new_status = {"accept": "accepted", "decline": "declined", "withdraw": "withdrawn"}[action]
            conn.execute("UPDATE demo_offers SET status=?, updated_at=? WHERE id=? AND status='pending'",
                         (new_status, now, offer_id))
            conn.commit()
            updated = conn.execute("""
                SELECT f.*, o.title AS order_title, o.user_id AS order_owner_id,
                       o.owner_name AS order_owner_name, o.status AS order_status
                FROM demo_offers f JOIN demo_orders o ON o.id = f.order_id WHERE f.id = ?
            """, (offer_id,)).fetchone()
        return {"item": _offer_dict(updated, actor_id), "simulation_only": True,
                "deal_created": action == "accept"}

    @router.get("/deals")
    async def list_deals(x_telegram_init_data: str | None = Header(default=None)):
        user = _require_user(x_telegram_init_data, verify_telegram_init_data)
        user_id = str(user["id"])
        with _connect() as conn:
            rows = conn.execute("""
                SELECT d.*, o.title AS order_title, o.owner_name AS order_owner_name,
                       f.sender_name AS offer_sender_name
                FROM demo_deals d
                JOIN demo_orders o ON o.id = d.order_id
                JOIN demo_offers f ON f.id = d.offer_id
                WHERE d.listing_owner_id = ? OR d.offer_sender_id = ?
                ORDER BY d.created_at DESC LIMIT 200
            """, (user_id, user_id)).fetchall()
        return {"items": [{
            "id": row["id"], "order_id": row["order_id"], "offer_id": row["offer_id"],
            "title": row["order_title"], "listing_owner": row["order_owner_name"],
            "offer_sender": row["offer_sender_name"], "amount": row["agreed_amount"],
            "currency": row["currency"], "created_at": row["created_at"],
            "status": "simulated_complete", "simulation_only": True,
        } for row in rows]}

    return router
