"""Shared server-side demo marketplace.

All amounts are labels for simulated TON / Telegram Stars offers only.
No wallet, payment, blockchain or transfer operations are performed.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
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


@contextmanager
def _db():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def init_marketplace_db() -> None:
    with _db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS telegram_users (
            telegram_id TEXT PRIMARY KEY,
            username TEXT NOT NULL DEFAULT '',
            first_name TEXT NOT NULL DEFAULT '',
            last_name TEXT NOT NULL DEFAULT '',
            photo_url TEXT NOT NULL DEFAULT '',
            language_code TEXT NOT NULL DEFAULT '',
            is_premium INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_telegram_users_updated ON telegram_users(updated_at DESC);
        CREATE TABLE IF NOT EXISTS user_favorites (
            telegram_id TEXT NOT NULL,
            gift_id TEXT NOT NULL,
            gift_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(telegram_id, gift_id)
        );
        CREATE INDEX IF NOT EXISTS idx_user_favorites_updated ON user_favorites(telegram_id, updated_at DESC);
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
        CREATE TABLE IF NOT EXISTS demo_order_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            actor_user_id TEXT NOT NULL,
            changed_fields TEXT NOT NULL DEFAULT '[]',
            created_at INTEGER NOT NULL,
            FOREIGN KEY(order_id) REFERENCES demo_orders(id)
        );
        CREATE INDEX IF NOT EXISTS idx_demo_order_revisions_order
            ON demo_order_revisions(order_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS api_rate_limits (
            user_id TEXT NOT NULL,
            action TEXT NOT NULL,
            window_started_at INTEGER NOT NULL,
            request_count INTEGER NOT NULL,
            PRIMARY KEY(user_id, action)
        );
        CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );
        """)



def get_app_setting(key: str, default: str = "") -> str:
    """Read a small durable runtime setting from SQLite."""
    safe_key = str(key or "").strip()[:100]
    if not safe_key:
        return default
    with _db() as conn:
        row = conn.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key = ?",
            (safe_key,),
        ).fetchone()
    return str(row["setting_value"]) if row is not None else default


def set_app_setting(key: str, value: str) -> None:
    """Persist a bounded runtime setting across backend restarts."""
    safe_key = str(key or "").strip()[:100]
    safe_value = str(value)[:500]
    if not safe_key:
        raise ValueError("A setting key is required")
    with _db() as conn:
        conn.execute(
            """INSERT INTO app_settings(setting_key, setting_value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(setting_key) DO UPDATE SET
                 setting_value=excluded.setting_value,
                 updated_at=excluded.updated_at""",
            (safe_key, safe_value, int(time.time())),
        )


def upsert_telegram_user(user: dict[str, Any]) -> dict[str, Any]:
    """Persist the minimum Telegram profile fields needed by the Mini App."""
    telegram_id = str(user.get("id", "")).strip()
    if not telegram_id.isdigit():
        raise ValueError("A verified Telegram user ID is required")
    now = int(time.time())
    values = (telegram_id, str(user.get("username") or "")[:64], str(user.get("first_name") or "")[:128], str(user.get("last_name") or "")[:128], str(user.get("photo_url") or "")[:2048], str(user.get("language_code") or "")[:16], 1 if user.get("is_premium") is True else 0, now, now)
    with _db() as conn:
        conn.execute("INSERT INTO telegram_users (telegram_id, username, first_name, last_name, photo_url, language_code, is_premium, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, last_name=excluded.last_name, photo_url=excluded.photo_url, language_code=excluded.language_code, is_premium=excluded.is_premium, updated_at=excluded.updated_at", values)
        row = conn.execute("SELECT * FROM telegram_users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    return {"id": row["telegram_id"], "username": row["username"], "first_name": row["first_name"], "last_name": row["last_name"], "photo_url": row["photo_url"], "language_code": row["language_code"], "is_premium": bool(row["is_premium"]), "created_at": row["created_at"], "updated_at": row["updated_at"]}

def _user(init_data: str | None, verify: Callable[[str], dict]) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="Open GIFT RADAR from Telegram to use demo orders")
    return verify(init_data)


def _enforce_user_rate_limit(user_id: str, action: str, limit: int, window_seconds: int = 60) -> None:
    """Persistent per-Telegram-user mutation limit shared across app restarts."""
    now = int(time.time())
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT window_started_at, request_count FROM api_rate_limits WHERE user_id = ? AND action = ?",
            (user_id, action),
        ).fetchone()
        if row is None or now - row["window_started_at"] >= window_seconds:
            conn.execute(
                """INSERT INTO api_rate_limits(user_id, action, window_started_at, request_count)
                   VALUES (?, ?, ?, 1)
                   ON CONFLICT(user_id, action) DO UPDATE SET
                   window_started_at=excluded.window_started_at, request_count=1""",
                (user_id, action, now),
            )
            conn.commit()
            return
        if row["request_count"] >= limit:
            retry_after = max(1, window_seconds - (now - row["window_started_at"]))
            conn.rollback()
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait before trying again.",
                headers={"Retry-After": str(retry_after)},
            )
        conn.execute(
            "UPDATE api_rate_limits SET request_count = request_count + 1 WHERE user_id = ? AND action = ?",
            (user_id, action),
        )
        conn.commit()


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



class UpdateDemoOrder(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    price: Decimal | None = None
    note: str | None = Field(default=None, max_length=300)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("Title cannot be null")
        value = value.strip()
        if not value:
            raise ValueError("Gift name is required")
        return value

    @field_validator("price")
    @classmethod
    def valid_price(cls, value: Decimal | None) -> Decimal | None:
        if value is None or not value.is_finite() or value <= 0 or value > Decimal("1000000000"):
            raise ValueError("Price must be a positive number")
        return value

    @field_validator("note")
    @classmethod
    def valid_note(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("Note cannot be null")
        return value.strip()

def create_marketplace_router(verify_telegram_init_data: Callable[[str], dict]) -> APIRouter:
    router = APIRouter(prefix="/api/marketplace", tags=["demo-marketplace"])

    @router.get("/favorites")
    async def list_favorites(x_telegram_init_data: str | None = Header(default=None)):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        with _db() as conn:
            rows = conn.execute(
                "SELECT gift_json FROM user_favorites WHERE telegram_id = ? ORDER BY updated_at DESC LIMIT 300",
                (str(user["id"]),),
            ).fetchall()
        import json
        items = []
        for row in rows:
            try:
                items.append(json.loads(row["gift_json"]))
            except (TypeError, ValueError):
                continue
        return {"items": items, "total": len(items)}

    @router.put("/favorites/{gift_id}")
    async def save_favorite(
        gift_id: str,
        payload: dict,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        _enforce_user_rate_limit(str(user["id"]), "save_favorite", 30)
        gift = payload.get("gift")
        if not isinstance(gift, dict):
            raise HTTPException(status_code=422, detail="A gift and its ID are required")
        if not gift_id or gift_id != gift_id.strip() or len(gift_id) > 160:
            raise HTTPException(status_code=422, detail="Gift ID must be between 1 and 160 characters")
        item_key = gift.get("id")
        if item_key is None:
            item_key = gift.get("number")
        if item_key is None:
            item_key = gift.get("name")
        if item_key is None:
            item_key = "gift"
        if not isinstance(item_key, (str, int, float)) or str(item_key) != gift_id:
            raise HTTPException(status_code=422, detail="Gift ID does not match the gift data")
        import json
        encoded = json.dumps(gift, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 16000:
            raise HTTPException(status_code=413, detail="Gift data is too large")
        now = int(time.time())
        with _db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute(
                "SELECT 1 FROM user_favorites WHERE telegram_id = ? AND gift_id = ?",
                (str(user["id"]), gift_id),
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) FROM user_favorites WHERE telegram_id = ?",
                (str(user["id"]),),
            ).fetchone()[0]
            if not exists and count >= 300:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Favorites limit reached")
            conn.execute(
                """INSERT INTO user_favorites(telegram_id, gift_id, gift_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(telegram_id, gift_id) DO UPDATE SET
                   gift_json=excluded.gift_json, updated_at=excluded.updated_at""",
                (str(user["id"]), gift_id, encoded, now, now),
            )
            conn.commit()
        return {"saved": True, "gift_id": gift_id}

    @router.delete("/favorites/{gift_id}")
    async def remove_favorite(
        gift_id: str,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        _enforce_user_rate_limit(str(user["id"]), "remove_favorite", 30)
        if not gift_id or gift_id != gift_id.strip() or len(gift_id) > 160:
            raise HTTPException(status_code=422, detail="Gift ID must be between 1 and 160 characters")
        with _db() as conn:
            conn.execute(
                "DELETE FROM user_favorites WHERE telegram_id = ? AND gift_id = ?",
                (str(user["id"]), gift_id),
            )
        return {"removed": True, "gift_id": gift_id}

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
        with _db() as conn:
            rows = conn.execute(
                f"SELECT * FROM demo_orders{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
            total = conn.execute(f"SELECT COUNT(*) FROM demo_orders{where}", values).fetchone()[0]
        return {"items": [_order_dict(row, user_id) for row in rows], "total": total}

    @router.get("/mine")
    async def my_orders(x_telegram_init_data: str | None = Header(default=None)):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        with _db() as conn:
            rows = conn.execute(
                "SELECT * FROM demo_orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 200",
                (str(user["id"]),),
            ).fetchall()
        return {"items": [_order_dict(row, str(user["id"])) for row in rows]}

    @router.get("/stats")
    async def marketplace_stats():
        with _db() as conn:
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
        _enforce_user_rate_limit(str(user["id"]), "create_order", 8)
        now = int(time.time())
        order_id = "gr-" + uuid.uuid4().hex[:16]
        owner = (user.get("first_name") or user.get("username") or "Telegram user")[:64]
        with _db() as conn:
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

    @router.patch("/orders/{order_id}")
    async def update_order(
        order_id: str,
        payload: UpdateDemoOrder,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        actor_id = str(user["id"])
        _enforce_user_rate_limit(actor_id, "update_order", 12)
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(status_code=422, detail="At least one order field must be provided")
        if any(value is None for value in changes.values()):
            raise HTTPException(status_code=422, detail="Order fields cannot be null")
        assignments, values = [], []
        for field, value in changes.items():
            if field == "price":
                value = format(value, "f")
            assignments.append(f"{field} = ?")
            values.append(value)
        now = int(time.time())
        assignments.append("updated_at = ?")
        values.extend([now, order_id])
        with _db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM demo_orders WHERE id = ?", (order_id,)).fetchone()
            if row is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Order not found")
            if row["user_id"] != actor_id:
                conn.rollback()
                raise HTTPException(status_code=403, detail="Only the author can edit this order")
            if row["status"] != "active":
                conn.rollback()
                raise HTTPException(status_code=409, detail="Only active orders can be edited")
            conn.execute(
                f"UPDATE demo_orders SET {', '.join(assignments)} WHERE id = ? AND status = 'active'",
                values,
            )
            updated = conn.execute("SELECT * FROM demo_orders WHERE id = ?", (order_id,)).fetchone()
            conn.execute(
                "INSERT INTO demo_order_revisions(order_id, actor_user_id, changed_fields, created_at) VALUES (?, ?, ?, ?)",
                (order_id, actor_id, json.dumps(sorted(changes.keys()), separators=(",", ":")), now),
            )
            conn.commit()
        return {"item": _order_dict(updated, actor_id)}

    @router.post("/orders/{order_id}/simulate-fill")
    async def simulate_fill(
        order_id: str,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        actor_id = str(user["id"])
        _enforce_user_rate_limit(actor_id, "simulate_fill", 12)
        now = int(time.time())
        with _db() as conn:
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
        _enforce_user_rate_limit(actor_id, "cancel_order", 12)
        now = int(time.time())
        with _db() as conn:
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
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        user_id = str(user["id"])
        with _db() as conn:
            rows = conn.execute(
                "SELECT * FROM demo_orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 200",
                (user_id,),
            ).fetchall()
            result = []
            for row in rows:
                events = conn.execute(
                    "SELECT event_type, created_at FROM demo_order_events WHERE order_id = ? ORDER BY created_at ASC, id ASC",
                    (row["id"],),
                ).fetchall()
                revisions = conn.execute(
                    "SELECT created_at FROM demo_order_revisions WHERE order_id = ? ORDER BY created_at ASC, id ASC",
                    (row["id"],),
                ).fetchall()
                item = _order_dict(row, user_id)
                timeline = [{"type": event["event_type"], "createdAt": event["created_at"]} for event in events]
                timeline.extend({"type": "updated", "createdAt": revision["created_at"]} for revision in revisions)
                item["events"] = sorted(timeline, key=lambda event: event["createdAt"])
                result.append(item)
        return {"items": result}

    return router
