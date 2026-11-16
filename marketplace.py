"""Shared server-side HASSLE NFT marketplace.

Listings store seller-provided prices in TON or Telegram Stars. Listing and offer records do not execute asset transfers or settle payments.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Literal, Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

DB_PATH = os.getenv("HASSLE_NFT_DB_PATH", os.getenv("GIFT_RADAR_DB_PATH", os.getenv("DATABASE_PATH", "./data/gift-radar.sqlite3")) )


def _connect_database(database_path: str | Path) -> sqlite3.Connection:
    """Open the shared SQLite database with explicit durability/contention settings.

    SQLite remains a single-writer, single-instance deployment baseline; these
    settings reduce transient lock failures but do not replace PostgreSQL.
    """
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def _connect() -> sqlite3.Connection:
    return _connect_database(DB_PATH)


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
        conn.execute("PRAGMA wal_autocheckpoint = 1000")
        conn.execute("PRAGMA optimize")
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
        CREATE TABLE IF NOT EXISTS user_profiles (
            profile_id TEXT PRIMARY KEY,
            telegram_id TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            bio TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            FOREIGN KEY(telegram_id) REFERENCES telegram_users(telegram_id)
        );
        CREATE INDEX IF NOT EXISTS idx_user_profiles_updated ON user_profiles(updated_at DESC);
        CREATE TABLE IF NOT EXISTS telegram_sessions (
            token_hash TEXT PRIMARY KEY,
            telegram_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            revoked_at INTEGER,
            FOREIGN KEY(telegram_id) REFERENCES telegram_users(telegram_id)
        );
        CREATE INDEX IF NOT EXISTS idx_telegram_sessions_user ON telegram_sessions(telegram_id, expires_at DESC);
        CREATE TABLE IF NOT EXISTS telegram_initdata_uses (
            initdata_hash TEXT PRIMARY KEY,
            telegram_id TEXT NOT NULL,
            consumed_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_telegram_initdata_expiry ON telegram_initdata_uses(expires_at);
        CREATE TABLE IF NOT EXISTS telegram_update_inbox (
            update_id INTEGER PRIMARY KEY,
            payload_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('processing', 'processed')),
            received_at INTEGER NOT NULL,
            lease_until INTEGER NOT NULL,
            processed_at INTEGER,
            attempts INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_telegram_update_inbox_status
            ON telegram_update_inbox(status, lease_until);
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
        CREATE TABLE IF NOT EXISTS demo_order_reports (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            reporter_user_id TEXT NOT NULL,
            reporter_name TEXT NOT NULL,
            reason TEXT NOT NULL CHECK(reason IN ('scam', 'misleading', 'inappropriate', 'spam', 'other')),
            details TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK(status IN ('open', 'dismissed', 'removed')),
            created_at INTEGER NOT NULL,
            resolved_at INTEGER,
            resolved_by TEXT,
            UNIQUE(order_id, reporter_user_id),
            FOREIGN KEY(order_id) REFERENCES demo_orders(id)
        );
        CREATE INDEX IF NOT EXISTS idx_demo_order_reports_status_created
            ON demo_order_reports(status, created_at DESC);
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
        # One-time cleanup of the explicitly identified test listing approved by the owner.
        # Match every identifying field so no other seller listing can be touched.
        conn.execute("BEGIN IMMEDIATE")
        test_rows = conn.execute(
            """SELECT id FROM demo_orders
               WHERE TRIM(title) = 'Plush Pepe #1'
                 AND LOWER(TRIM(owner_name)) = 'n0kl1pzz1k'
                 AND currency = 'TON'
                 AND CAST(price AS REAL) = 70000"""
        ).fetchall()
        test_ids = [row["id"] for row in test_rows]
        for test_id in test_ids:
            conn.execute("DELETE FROM demo_order_reports WHERE order_id = ?", (test_id,))
            conn.execute("DELETE FROM demo_order_events WHERE order_id = ?", (test_id,))
            conn.execute("DELETE FROM demo_order_revisions WHERE order_id = ?", (test_id,))
            conn.execute("DELETE FROM demo_orders WHERE id = ?", (test_id,))
        conn.commit()



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


def consume_telegram_init_data(init_data: str, telegram_id: str, ttl_seconds: int = 900) -> bool:
    """Atomically mark a verified initData signature as exchanged, independent of query order."""
    from urllib.parse import parse_qsl

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True, max_num_fields=64)
        values: dict[str, str] = {}
        for key, value in pairs:
            if not key or key in values:
                raise ValueError("Duplicate initData field")
            values[key] = value
        signature = values.get("hash")
        if not signature or len(signature) != 64:
            raise ValueError("Missing Telegram signature")
        replay_material = signature.lower()
    except ValueError:
        # Test-only compatibility for mocked auth callbacks; production verification
        # rejects malformed raw initData before this function is called.
        replay_material = init_data
    digest = hashlib.sha256(replay_material.encode("utf-8")).hexdigest()
    now = int(time.time())
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM telegram_initdata_uses WHERE expires_at <= ?", (now,))
        try:
            conn.execute(
                "INSERT INTO telegram_initdata_uses(initdata_hash, telegram_id, consumed_at, expires_at) VALUES (?, ?, ?, ?)",
                (digest, str(telegram_id), now, now + max(300, min(int(ttl_seconds), 3600))),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            return False
        conn.commit()
    return True


def create_telegram_session(telegram_id: str, ttl_seconds: int = 43200) -> str:
    """Create an opaque bearer session; only its SHA-256 digest is stored."""
    safe_id = str(telegram_id).strip()
    if not safe_id.isdigit():
        raise ValueError("A verified Telegram user ID is required")
    now = int(time.time())
    ttl = max(300, min(int(ttl_seconds), 43200))
    token = "grs_" + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _db() as conn:
        conn.execute(
            "INSERT INTO telegram_sessions(token_hash, telegram_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, safe_id, now, now + ttl),
        )
    return token


def get_telegram_session_user(token: str) -> dict[str, Any] | None:
    """Resolve an unexpired session without ever storing or logging its bearer token."""
    if not isinstance(token, str) or not token.startswith("grs_") or len(token) > 128:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = int(time.time())
    with _db() as conn:
        row = conn.execute(
            """SELECT u.telegram_id, u.username, u.first_name, u.last_name,
                      u.photo_url, u.language_code, u.is_premium
               FROM telegram_sessions s
               JOIN telegram_users u ON u.telegram_id = s.telegram_id
               WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ?""",
            (token_hash, now),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["telegram_id"],
        "username": row["username"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "photo_url": row["photo_url"],
        "language_code": row["language_code"],
        "is_premium": bool(row["is_premium"]),
    }


def revoke_telegram_session(token: str) -> bool:
    if not isinstance(token, str) or not token.startswith("grs_") or len(token) > 128:
        return False
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _db() as conn:
        cursor = conn.execute(
            "UPDATE telegram_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (int(time.time()), token_hash),
        )
    return cursor.rowcount == 1


def list_telegram_sessions(telegram_id: str, current_token: str | None = None) -> list[dict[str, Any]]:
    """Return safe session metadata only; never expose bearer tokens or token hashes."""
    current_hash = hashlib.sha256(current_token.encode("utf-8")).hexdigest() if current_token else None
    now = int(time.time())
    with _db() as conn:
        rows = conn.execute(
            """SELECT token_hash, created_at, expires_at, revoked_at
               FROM telegram_sessions WHERE telegram_id = ?
               ORDER BY created_at DESC LIMIT 20""",
            (str(telegram_id),),
        ).fetchall()
    return [{
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "revoked_at": row["revoked_at"],
        "active": row["revoked_at"] is None and row["expires_at"] > now,
        "current": current_hash is not None and row["token_hash"] == current_hash,
    } for row in rows]


def revoke_all_telegram_sessions(telegram_id: str, except_token: str | None = None) -> int:
    """Revoke every active session for one account, optionally retaining the caller session."""
    now = int(time.time())
    params: list[Any] = [now, str(telegram_id), now]
    exclusion = ""
    if except_token:
        token_hash = hashlib.sha256(except_token.encode("utf-8")).hexdigest()
        exclusion = " AND token_hash != ?"
        params.append(token_hash)
    with _db() as conn:
        cursor = conn.execute(
            f"""UPDATE telegram_sessions SET revoked_at = ?
                WHERE telegram_id = ? AND revoked_at IS NULL AND expires_at > ?{exclusion}""",
            tuple(params),
        )
    return cursor.rowcount


def claim_telegram_update(update_id: int, payload: Any, lease_seconds: int = 60) -> str:
    """Atomically deduplicate Telegram webhook updates and allow retry after a stale lease."""
    if isinstance(update_id, bool) or not isinstance(update_id, int) or update_id < 0:
        raise ValueError("Invalid Telegram update ID")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    now = int(time.time())
    lease = max(15, min(int(lease_seconds), 300))
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM telegram_update_inbox WHERE status = 'processed' AND processed_at < ?",
            (now - 1_209_600,),
        )
        row = conn.execute(
            "SELECT payload_hash, status, lease_until FROM telegram_update_inbox WHERE update_id = ?",
            (update_id,),
        ).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO telegram_update_inbox
                   (update_id, payload_hash, status, received_at, lease_until, attempts)
                   VALUES (?, ?, 'processing', ?, ?, 1)""",
                (update_id, digest, now, now + lease),
            )
            conn.commit()
            return "claimed"
        if not secrets.compare_digest(row["payload_hash"], digest):
            conn.rollback()
            return "conflict"
        if row["status"] == "processed":
            conn.commit()
            return "processed"
        if row["lease_until"] > now:
            conn.commit()
            return "in_progress"
        conn.execute(
            """UPDATE telegram_update_inbox
               SET lease_until = ?, attempts = attempts + 1
               WHERE update_id = ? AND status = 'processing'""",
            (now + lease, update_id),
        )
        conn.commit()
        return "claimed"


def finish_telegram_update(update_id: int) -> None:
    with _db() as conn:
        conn.execute(
            """UPDATE telegram_update_inbox
               SET status = 'processed', processed_at = ?, lease_until = 0
               WHERE update_id = ?""",
            (int(time.time()), update_id),
        )


def release_telegram_update(update_id: int) -> None:
    """Release a failed update for Telegram retry without recording exception contents."""
    with _db() as conn:
        conn.execute(
            "UPDATE telegram_update_inbox SET lease_until = 0 WHERE update_id = ? AND status = 'processing'",
            (update_id,),
        )


def _user(init_data: str | None, verify: Callable[[str], dict]) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="Open HASSLE NFT from Telegram to manage marketplace listings")
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
        "seller_profile_id": row["seller_profile_id"] if "seller_profile_id" in row.keys() else None,
        "isMine": current_user_id is not None and row["user_id"] == current_user_id,
    }


class UpdateSellerProfile(BaseModel):
    display_name: str = Field(min_length=1, max_length=48)
    bio: str = Field(default="", max_length=280)

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Display name is required")
        return value

    @field_validator("bio")
    @classmethod
    def clean_bio(cls, value: str) -> str:
        return value.strip()


class CreateMarketplaceOrder(BaseModel):
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



class UpdateMarketplaceOrder(BaseModel):
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

class CreateOrderReport(BaseModel):
    reason: Literal["scam", "misleading", "inappropriate", "spam", "other"]
    details: str = Field(default="", max_length=500)

    @field_validator("details")
    @classmethod
    def clean_details(cls, value: str) -> str:
        return value.strip()


class ModerateOrderReport(BaseModel):
    action: Literal["dismiss", "remove_order"]


def create_marketplace_router(
    verify_telegram_init_data: Callable[[str], dict],
    verify_admin: Callable[[str | None], dict] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])

    @router.get("/profiles/me")
    async def get_my_seller_profile(
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        telegram_id = str(user["id"])
        now = int(time.time())
        fallback_name = (user.get("first_name") or user.get("username") or "Telegram user")[:48]
        with _db() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO user_profiles
                   (profile_id, telegram_id, display_name, bio, created_at, updated_at)
                   VALUES (?, ?, ?, '', ?, ?)""",
                ("usr_" + secrets.token_urlsafe(12), telegram_id, fallback_name, now, now),
            )
            row = conn.execute(
                """SELECT p.profile_id, p.display_name, p.bio, p.created_at, p.updated_at,
                          u.username
                   FROM user_profiles p JOIN telegram_users u ON u.telegram_id = p.telegram_id
                   WHERE p.telegram_id = ?""",
                (telegram_id,),
            ).fetchone()
        return {"profile": dict(row)}

    @router.put("/profiles/me")
    async def update_my_seller_profile(
        payload: UpdateSellerProfile,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        telegram_id = str(user["id"])
        _enforce_user_rate_limit(telegram_id, "update_seller_profile", 8)
        now = int(time.time())
        fallback_name = (user.get("first_name") or user.get("username") or "Telegram user")[:48]
        with _db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT OR IGNORE INTO user_profiles
                   (profile_id, telegram_id, display_name, bio, created_at, updated_at)
                   VALUES (?, ?, ?, '', ?, ?)""",
                ("usr_" + secrets.token_urlsafe(12), telegram_id, fallback_name, now, now),
            )
            conn.execute(
                """UPDATE user_profiles SET display_name = ?, bio = ?, updated_at = ?
                   WHERE telegram_id = ?""",
                (payload.display_name, payload.bio, now, telegram_id),
            )
            row = conn.execute(
                """SELECT p.profile_id, p.display_name, p.bio, p.created_at, p.updated_at,
                          u.username
                   FROM user_profiles p JOIN telegram_users u ON u.telegram_id = p.telegram_id
                   WHERE p.telegram_id = ?""",
                (telegram_id,),
            ).fetchone()
            conn.commit()
        return {"profile": dict(row)}

    @router.get("/profiles/{profile_id}")
    async def get_public_seller_profile(profile_id: str):
        if not profile_id.startswith("usr_") or len(profile_id) > 64:
            raise HTTPException(status_code=404, detail="Seller profile not found")
        with _db() as conn:
            row = conn.execute(
                """SELECT p.profile_id, p.display_name, p.bio, p.created_at,
                          u.username,
                          (SELECT COUNT(*) FROM demo_orders o
                           WHERE o.user_id = p.telegram_id AND o.kind = 'listing'
                             AND o.status = 'active') AS active_listings
                   FROM user_profiles p JOIN telegram_users u ON u.telegram_id = p.telegram_id
                   WHERE p.profile_id = ?""",
                (profile_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Seller profile not found")
            listing_rows = conn.execute(
                """SELECT o.id, o.title, o.price, o.currency, o.created_at, o.note
                   FROM demo_orders o
                   JOIN user_profiles p ON p.telegram_id = o.user_id
                   WHERE p.profile_id = ? AND o.kind = 'listing' AND o.status = 'active'
                   ORDER BY o.created_at DESC LIMIT 100""",
                (profile_id,),
            ).fetchall()
        # Public projection is allow-listed: no Telegram numeric ID, session data,
        # buyer requests, private fields or unverified reputation.
        public_listings = [{
            "id": item["id"], "title": item["title"], "price": item["price"],
            "currency": item["currency"], "createdAt": item["created_at"], "note": item["note"],
        } for item in listing_rows]
        return {"profile": {
            **dict(row), "completed_trades": 0, "rating": None, "listings": public_listings
        }}



    @router.post("/orders/{order_id}/reports", status_code=201)
    async def create_order_report(
        order_id: str,
        payload: CreateOrderReport,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        reporter_id = str(user["id"])
        _enforce_user_rate_limit(reporter_id, "create_order_report", 5)
        now = int(time.time())
        with _db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            order = conn.execute("SELECT * FROM demo_orders WHERE id = ?", (order_id,)).fetchone()
            if order is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Demo order not found")
            if order["user_id"] == reporter_id:
                conn.rollback()
                raise HTTPException(status_code=403, detail="You cannot report your own order")
            if order["status"] != "active":
                conn.rollback()
                raise HTTPException(status_code=409, detail="Only active orders can be reported")
            duplicate = conn.execute(
                "SELECT 1 FROM demo_order_reports WHERE order_id = ? AND reporter_user_id = ?",
                (order_id, reporter_id),
            ).fetchone()
            if duplicate:
                conn.rollback()
                raise HTTPException(status_code=409, detail="You have already reported this order")
            report_count = conn.execute(
                "SELECT COUNT(*) FROM demo_order_reports WHERE order_id = ? AND status = 'open'",
                (order_id,),
            ).fetchone()[0]
            if report_count >= 25:
                conn.rollback()
                raise HTTPException(status_code=409, detail="This order has reached its report limit")
            report_id = "rp-" + uuid.uuid4().hex[:16]
            reporter_name = (user.get("first_name") or user.get("username") or "Telegram user")[:64]
            conn.execute(
                """INSERT INTO demo_order_reports
                   (id, order_id, reporter_user_id, reporter_name, reason, details, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'open', ?)""",
                (report_id, order_id, reporter_id, reporter_name, payload.reason, payload.details, now),
            )
            conn.commit()
        return {"report": {"id": report_id, "order_id": order_id, "status": "open", "created_at": now}}

    @router.get("/admin/reports")
    async def list_order_reports(
        status: str = Query(default="open", pattern="^(open|dismissed|removed|all)$"),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=10000),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        if verify_admin is None:
            raise HTTPException(status_code=503, detail="Admin moderation is not configured")
        verify_admin(x_telegram_init_data)
        where = ""
        values: list[object] = []
        if status != "all":
            where = " WHERE r.status = ?"
            values.append(status)
        with _db() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM demo_order_reports r{where}", values,
            ).fetchone()[0]
            rows = conn.execute(
                f"""SELECT r.id, r.order_id, r.reporter_name, r.reason, r.details,
                           r.status, r.created_at, r.resolved_at, o.title AS order_title,
                           o.status AS order_status, o.owner_name AS order_owner
                    FROM demo_order_reports r JOIN demo_orders o ON o.id = r.order_id
                    {where} ORDER BY r.created_at DESC, r.id DESC LIMIT ? OFFSET ?""",
                (*values, limit, offset),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    @router.patch("/admin/reports/{report_id}")
    async def moderate_order_report(
        report_id: str,
        payload: ModerateOrderReport,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        if verify_admin is None:
            raise HTTPException(status_code=503, detail="Admin moderation is not configured")
        admin = verify_admin(x_telegram_init_data)
        admin_id = str(admin["id"])
        _enforce_user_rate_limit(admin_id, "moderate_order_report", 30)
        now = int(time.time())
        with _db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            report = conn.execute(
                """SELECT r.*, o.status AS order_status FROM demo_order_reports r
                   JOIN demo_orders o ON o.id = r.order_id WHERE r.id = ?""",
                (report_id,),
            ).fetchone()
            if report is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Report not found")
            if report["status"] != "open":
                conn.rollback()
                raise HTTPException(status_code=409, detail="This report has already been resolved")
            if payload.action == "remove_order":
                if report["order_status"] != "active":
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="Only active orders can be removed")
                conn.execute(
                    "UPDATE demo_orders SET status='cancelled', updated_at=? WHERE id=? AND status='active'",
                    (now, report["order_id"]),
                )
                conn.execute(
                    "INSERT INTO demo_order_events(order_id, actor_user_id, event_type, created_at) VALUES (?, ?, 'cancelled', ?)",
                    (report["order_id"], admin_id, now),
                )
                new_status = "removed"
            else:
                new_status = "dismissed"
            conn.execute(
                "UPDATE demo_order_reports SET status=?, resolved_at=?, resolved_by=? WHERE id=? AND status='open'",
                (new_status, now, admin_id, report_id),
            )
            conn.commit()
        return {"report_id": report_id, "status": new_status, "order_removed": payload.action == "remove_order"}

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
            qualified_where = where.replace("status", "o.status").replace("kind", "o.kind")
            rows = conn.execute(
                f"""SELECT o.*, p.profile_id AS seller_profile_id
                    FROM demo_orders o
                    LEFT JOIN user_profiles p ON p.telegram_id = o.user_id
                    {qualified_where}
                    ORDER BY o.created_at DESC LIMIT ? OFFSET ?""",
                (*values, limit, offset),
            ).fetchall()
            total = conn.execute(f"SELECT COUNT(*) FROM demo_orders{where}", values).fetchone()[0]
        return {"items": [_order_dict(row, user_id) for row in rows], "total": total}

    @router.get("/mine")
    async def my_orders(x_telegram_init_data: str | None = Header(default=None)):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        with _db() as conn:
            rows = conn.execute(
                """SELECT o.*, p.profile_id AS seller_profile_id
                   FROM demo_orders o
                   LEFT JOIN user_profiles p ON p.telegram_id = o.user_id
                   WHERE o.user_id = ? ORDER BY o.created_at DESC LIMIT 200""",
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
                    MIN(CASE WHEN status='active' AND kind='listing' AND currency='TON' THEN CAST(price AS REAL) END) AS floor_ton,
                    MIN(CASE WHEN status='active' AND kind='listing' AND currency='XTR' THEN CAST(price AS REAL) END) AS floor_stars,
                    COUNT(*) AS total_orders
                FROM demo_orders
            """).fetchone()
        return {key: (counts[key] or 0) for key in counts.keys()}

    @router.post("/orders", status_code=201)
    async def create_order(
        payload: CreateMarketplaceOrder,
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
        payload: UpdateMarketplaceOrder,
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
