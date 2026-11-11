"""Shared server-side demo marketplace.

All amounts are labels for simulated TON / Telegram Stars offers only.
No wallet, payment, blockchain or transfer operations are performed.
"""
from __future__ import annotations

import os
import sqlite3
import time
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Literal, Any

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


def init_marketplace_db() -> None:
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS demo_orders (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            owner_name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('listing', 'buy-order')),
            title TEXT NOT NULL,
            price TEXT NOT NULL,
            currency TEXT NOT NULL CHECK(currency IN ('TON', 'XTR')),
            status TEXT NOT NULL CHECK(status IN ('active', 'matched', 'cancelled')),
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            note TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_demo_orders_status_created
            ON demo_orders(status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_demo_orders_user_created
            ON demo_orders(user_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS demo_order_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            actor_user_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN ('created', 'simulated_fill', 'cancelled')),
            created_at INTEGER NOT NULL,
            FOREIGN KEY(order_id) REFERENCES demo_orders(id)
        );
        CREATE INDEX IF NOT EXISTS idx_demo_order_events_order
            ON demo_order_events(order_id, created_at DESC);
        """)


def _user(init_data: str | None, verify: Callable[[str], dict]) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="Open GIFT RADAR from Telegram to use demo orders")
    return verify(init_data)


def _order_dict(row: sqlite3.Row, current_user_id: str | None = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "price": row["price"],
        "currency": row["currency"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "note": row["note"],
        "owner": row["owner_name"],
        "isMine": current_user_id is not None and row["user_id"] == current_user_id,
    }


class CreateDemoOrder(BaseModel):
    kind: Literal["listing", "buy-order"]
    title: str = Field(min_length=1, max_length=120)
    price: Decimal
    currency: Literal["TON", "XTR"]
    note: str = Field(default="", max_length=300)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Gift name is required")
        return value

    @field_validator("price")
    @classmethod
    def valid_price(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0 or value > Decimal("1000000000"):
            raise ValueError("Price must be a positive number")
        return value


def create_marketplace_router(verify_telegram_init_data: Callable[[str], dict]) -> APIRouter:
    router = APIRouter(prefix="/api/marketplace", tags=["demo-marketplace"])

    @router.get("/orders")
    async def list_orders(
        status: str = Query(default="active", pattern="^(active|matched|cancelled|all)$"),
        kind: str | None = Query(default=None, pattern="^(listing|buy-order)$"),
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=10000),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user_id = None
        if x_telegram_init_data:
            try:
                user_id = str(verify_telegram_init_data(x_telegram_init_data)["id"])
            except HTTPException:
                user_id = None
        clauses, values = [], []
        if status != "all":
            clauses.append("status = ?")
            values.append(status)
        if kind:
            clauses.append("kind = ?")
            values.append(kind)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM demo_orders{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
            total = conn.execute(f"SELECT COUNT(*) FROM demo_orders{where}", values).fetchone()[0]
        return {"items": [_order_dict(row, user_id) for row in rows], "total": total}

    @router.get("/mine")
    async def my_orders(x_telegram_init_data: str | None = Header(default=None)):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        with _connect() as conn:
            rows = conn.execute(
                "SELECT * FROM demo_orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 200",
                (str(user["id"]),),
            ).fetchall()
        return {"items": [_order_dict(row, str(user["id"])) for row in rows]}

    @router.get("/stats")
    async def marketplace_stats():
        with _connect() as conn:
            counts = conn.execute("""
                SELECT
                    SUM(CASE WHEN status='active' AND kind='listing' THEN 1 ELSE 0 END) AS active_listings,
                    SUM(CASE WHEN status='active' AND kind='buy-order' THEN 1 ELSE 0 END) AS active_buy_requests,
                    SUM(CASE WHEN status='matched' THEN 1 ELSE 0 END) AS matched_orders,
                    SUM(CASE WHEN status='matched' AND currency='TON' THEN CAST(price AS REAL) ELSE 0 END) AS volume_ton,
                    SUM(CASE WHEN status='matched' AND currency='XTR' THEN CAST(price AS REAL) ELSE 0 END) AS volume_stars,
                    COUNT(*) AS total_orders
                FROM demo_orders
            """).fetchone()
        return {key: (counts[key] or 0) for key in counts.keys()}

    @router.post("/orders", status_code=201)
    async def create_order(
        payload: CreateDemoOrder,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        now = int(time.time())
        order_id = "gr-" + uuid.uuid4().hex[:16]
        owner = (user.get("first_name") or user.get("username") or "Telegram user")[:64]
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                INSERT INTO demo_orders
                (id, user_id, owner_name, kind, title, price, currency, status, created_at, updated_at, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """, (order_id, str(user["id"]), owner, payload.kind, payload.title,
                  format(payload.price, "f"), payload.currency, now, now, payload.note.strip()))
            conn.execute(
                "INSERT INTO demo_order_events(order_id, actor_user_id, event_type, created_at) VALUES (?, ?, 'created', ?)",
                (order_id, str(user["id"]), now),
            )
            row = conn.execute("SELECT * FROM demo_orders WHERE id = ?", (order_id,)).fetchone()
            conn.commit()
        return {"item": _order_dict(row, str(user["id"]))}

    @router.post("/orders/{order_id}/simulate-fill")
    async def simulate_fill(
        order_id: str,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        actor_id = str(user["id"])
        now = int(time.time())
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM demo_orders WHERE id = ?", (order_id,)).fetchone()
            if row is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Order not found")
            if row["user_id"] == actor_id:
                conn.rollback()
                raise HTTPException(status_code=403, detail="You cannot fill your own demo order")
            updated = conn.execute(
                "UPDATE demo_orders SET status='matched', updated_at=? WHERE id=? AND status='active'",
                (now, order_id),
            )
            if updated.rowcount != 1:
                conn.rollback()
                raise HTTPException(status_code=409, detail="This order is no longer active")
            conn.execute(
                "INSERT INTO demo_order_events(order_id, actor_user_id, event_type, created_at) VALUES (?, ?, 'simulated_fill', ?)",
                (order_id, actor_id, now),
            )
            row = conn.execute("SELECT * FROM demo_orders WHERE id = ?", (order_id,)).fetchone()
            conn.commit()
        return {"item": _order_dict(row, actor_id), "simulated": True}

    @router.delete("/orders/{order_id}")
    async def cancel_order(
        order_id: str,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        actor_id = str(user["id"])
        now = int(time.time())
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM demo_orders WHERE id = ?", (order_id,)).fetchone()
            if row is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Order not found")
            if row["user_id"] != actor_id:
                conn.rollback()
                raise HTTPException(status_code=403, detail="Only the author can cancel this order")
            updated = conn.execute(
                "UPDATE demo_orders SET status='cancelled', updated_at=? WHERE id=? AND status='active'",
                (now, order_id),
            )
            if updated.rowcount != 1:
                conn.rollback()
                raise HTTPException(status_code=409, detail="This order is no longer active")
            conn.execute(
                "INSERT INTO demo_order_events(order_id, actor_user_id, event_type, created_at) VALUES (?, ?, 'cancelled', ?)",
                (order_id, actor_id, now),
            )
            row = conn.execute("SELECT * FROM demo_orders WHERE id = ?", (order_id,)).fetchone()
            conn.commit()
        return {"item": _order_dict(row, actor_id)}

    @router.get("/history")
    async def history(x_telegram_init_data: str | None = Header(default=None)):
        return await my_orders(x_telegram_init_data)

    return router
