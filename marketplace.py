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
from urllib.parse import urlparse
from decimal import Decimal, InvalidOperation
from typing import Callable, Literal, Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from database import DB_PATH, begin_write, select_for_update, require_sqlite_connection, is_integrity_error, db as _database_db


def _db():
    # Preserve the legacy path override while centralizing connection lifetime.
    return _database_db(DB_PATH)

_SECURITY_AUDIT_ACTIONS = {
    "telegram_session_revoked",
    "telegram_sessions_revoked_all",
    "ton_wallet_linked",
    "ton_wallet_revoked",
    "admin_report_dismissed",
    "admin_order_removed",
    "admin_asset_revoked",
}
_SECURITY_AUDIT_TARGET_TYPES = {"session", "account", "wallet", "order_report", "order", "verified_asset"}
_SECURITY_AUDIT_METADATA_KEYS = {"network", "report_id", "order_id", "revoked_count", "cancelled_listings", "reason"}


def insert_security_audit_event(
    conn: Any,
    *,
    actor_telegram_id: str,
    action: str,
    target_type: str,
    target_id: str,
    outcome: str = "success",
    metadata: dict[str, Any] | None = None,
    now: int | None = None,
) -> str:
    """Insert an append-only audit record without storing bearer credentials or raw targets."""
    actor = str(actor_telegram_id).strip()
    target = str(target_id or "").strip()
    if not actor.isdigit() or not target or len(target) > 512:
        raise ValueError("A valid audit actor and target are required")
    if action not in _SECURITY_AUDIT_ACTIONS or target_type not in _SECURITY_AUDIT_TARGET_TYPES:
        raise ValueError("Unsupported security audit action or target type")
    if outcome not in {"success", "denied"}:
        raise ValueError("Unsupported security audit outcome")

    safe_metadata: dict[str, str | int | bool] = {}
    for key, value in (metadata or {}).items():
        if key not in _SECURITY_AUDIT_METADATA_KEYS:
            continue
        if key == "network" and isinstance(value, str) and value in {"-239", "-3"}:
            safe_metadata[key] = value
        elif key in {"report_id", "order_id"} and isinstance(value, str) and 1 <= len(value) <= 128:
            if all(character.isalnum() or character in "_-" for character in value):
                safe_metadata[key] = value
        elif key in {"revoked_count", "cancelled_listings"} and isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1000:
            safe_metadata[key] = value
        elif key == "reason" and value in {"self_logout", "asset_moderation"}:
            safe_metadata[key] = value
    target_hash = hashlib.sha256(f"{target_type}:{target}".encode("utf-8")).hexdigest()
    event_id = "sa_" + uuid.uuid4().hex[:24]
    timestamp = int(time.time()) if now is None else int(now)
    conn.execute(
        """INSERT INTO security_audit_events
           (id, actor_telegram_id, action, target_type, target_hash, outcome, metadata_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_id, actor, action, target_type, target_hash, outcome,
         json.dumps(safe_metadata, sort_keys=True, separators=(",", ":")), timestamp),
    )
    return event_id


def record_security_audit_event(**kwargs: Any) -> str:
    """Write a security audit event in its own transaction."""
    with _db() as conn:
        begin_write(conn, f"security-audit:{kwargs.get('actor_telegram_id', '')}:{kwargs.get('action', '')}")
        event_id = insert_security_audit_event(conn, **kwargs)
        conn.commit()
    return event_id


_LEGACY_MARKETPLACE_TABLE_RENAMES = (
    ("demo_orders", "marketplace_orders"),
    ("demo_order_events", "marketplace_order_events"),
    ("demo_order_revisions", "marketplace_order_revisions"),
    ("demo_order_reports", "marketplace_order_reports"),
    ("demo_offers", "marketplace_offers"),
    ("demo_deals", "marketplace_deals"),
    ("demo_deal_events", "marketplace_deal_events"),
    ("demo_deal_lifecycle_events", "marketplace_deal_lifecycle_events"),
    ("demo_notifications", "marketplace_notifications"),
)
_LEGACY_MARKETPLACE_INDEXES = (
    "idx_demo_orders_status_created", "idx_demo_orders_user_created", "idx_demo_orders_asset",
    "idx_demo_order_events_order", "idx_demo_order_revisions_order",
    "idx_demo_order_reports_status_created", "idx_demo_offers_order",
    "idx_demo_offers_sender", "idx_demo_offers_status",
    "idx_demo_deal_events_deal_created", "idx_demo_deal_lifecycle_events_deal",
    "idx_demo_notifications_user_created", "idx_demo_notifications_user_unread",
    "idx_demo_deals_one_open_reservation",
)
_LEGACY_MARKETPLACE_TRIGGERS = (
    "trg_demo_deal_events_no_update", "trg_demo_deal_events_no_delete",
    "trg_demo_deal_lifecycle_events_no_update", "trg_demo_deal_lifecycle_events_no_delete",
    "trg_demo_offer_received", "trg_demo_offer_status_notification",
    "trg_demo_order_cancel_declines_offers",
)


def _migrate_legacy_marketplace_table_names(conn: sqlite3.Connection) -> None:
    require_sqlite_connection(conn, "Legacy marketplace schema migration")
    """Rename legacy demo-prefixed marketplace tables without copying or deleting rows.

    SQLite updates foreign-key references during ALTER TABLE RENAME. If both names
    exist, fail closed rather than guessing which table contains the authoritative
    production records.
    """
    existing = {
        row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for legacy_name, canonical_name in _LEGACY_MARKETPLACE_TABLE_RENAMES:
        if legacy_name in existing and canonical_name in existing:
            raise RuntimeError(
                f"Both legacy and canonical marketplace tables exist: {canonical_name}"
            )
        if legacy_name in existing:
            conn.execute(
                f'ALTER TABLE "{legacy_name}" RENAME TO "{canonical_name}"'
            )
            existing.remove(legacy_name)
            existing.add(canonical_name)
    for trigger_name in _LEGACY_MARKETPLACE_TRIGGERS:
        conn.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')
    for index_name in _LEGACY_MARKETPLACE_INDEXES:
        conn.execute(f'DROP INDEX IF EXISTS "{index_name}"')


def _migrate_legacy_order_event_types(conn: sqlite3.Connection) -> None:
    require_sqlite_connection(conn, "Legacy marketplace event migration")
    """Preserve old synthetic-fill history as non-public, unverified audit rows."""
    table = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='marketplace_order_events'"
    ).fetchone()
    if table is None or "simulated_fill" not in (table["sql"] or ""):
        return
    begin_write(conn, "schema:legacy-order-event-types")
    try:
        conn.execute("""
            CREATE TABLE marketplace_order_events_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                actor_user_id TEXT NOT NULL,
                event_type TEXT NOT NULL
                    CHECK(event_type IN ('created', 'filled', 'cancelled', 'legacy_unverified')),
                created_at INTEGER NOT NULL,
                FOREIGN KEY(order_id) REFERENCES marketplace_orders(id)
            )
        """)
        conn.execute("""
            INSERT INTO marketplace_order_events_new
                (id, order_id, actor_user_id, event_type, created_at)
            SELECT id, order_id, actor_user_id,
                   CASE WHEN event_type='simulated_fill'
                        THEN 'legacy_unverified' ELSE event_type END,
                   created_at
            FROM marketplace_order_events
        """)
        conn.execute("DROP TABLE marketplace_order_events")
        conn.execute(
            "ALTER TABLE marketplace_order_events_new RENAME TO marketplace_order_events"
        )
        conn.execute("""
            CREATE INDEX idx_marketplace_order_events_order
            ON marketplace_order_events(order_id, created_at DESC)
        """)
        conn.commit()
    except Exception:
        conn.rollback()
        raise





def _migrate_security_audit_schema(conn: sqlite3.Connection) -> None:
    """Expand the append-only audit allow-list without losing historical events."""
    require_sqlite_connection(conn, "Security audit schema migration")
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='security_audit_events'"
    ).fetchone()
    schema_sql = (row["sql"] or "") if row else ""
    if "admin_asset_revoked" in schema_sql and "verified_asset" in schema_sql:
        return
    conn.execute("DROP TRIGGER IF EXISTS security_audit_events_no_update")
    conn.execute("DROP TRIGGER IF EXISTS security_audit_events_no_delete")
    conn.execute("DROP INDEX IF EXISTS idx_security_audit_actor_created")
    conn.execute("DROP INDEX IF EXISTS idx_security_audit_action_created")
    conn.execute("ALTER TABLE security_audit_events RENAME TO security_audit_events_legacy_admin_v1")
    conn.execute("""
        CREATE TABLE security_audit_events (
            id TEXT PRIMARY KEY,
            actor_telegram_id TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN (
                'telegram_session_revoked', 'telegram_sessions_revoked_all',
                'ton_wallet_linked', 'ton_wallet_revoked',
                'admin_report_dismissed', 'admin_order_removed', 'admin_asset_revoked'
            )),
            target_type TEXT NOT NULL CHECK(target_type IN (
                'session', 'account', 'wallet', 'order_report', 'order', 'verified_asset'
            )),
            target_hash TEXT NOT NULL CHECK(length(target_hash) = 64),
            outcome TEXT NOT NULL CHECK(outcome IN ('success', 'denied')),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL
        )
    """)
    conn.execute("""
        INSERT INTO security_audit_events
        (id, actor_telegram_id, action, target_type, target_hash, outcome, metadata_json, created_at)
        SELECT id, actor_telegram_id, action, target_type, target_hash, outcome, metadata_json, created_at
        FROM security_audit_events_legacy_admin_v1
    """)
    conn.execute("DROP TABLE security_audit_events_legacy_admin_v1")
    conn.execute("CREATE INDEX idx_security_audit_actor_created ON security_audit_events(actor_telegram_id, created_at DESC)")
    conn.execute("CREATE INDEX idx_security_audit_action_created ON security_audit_events(action, created_at DESC)")
    conn.execute("""
        CREATE TRIGGER security_audit_events_no_update
        BEFORE UPDATE ON security_audit_events
        BEGIN SELECT RAISE(ABORT, 'security audit events are immutable'); END
    """)
    conn.execute("""
        CREATE TRIGGER security_audit_events_no_delete
        BEFORE DELETE ON security_audit_events
        BEGIN SELECT RAISE(ABORT, 'security audit events are immutable'); END
    """)


def init_marketplace_db() -> None:
    with _db() as conn:
        if bool(getattr(conn, "supports_for_update", False)):
            from marketplace_postgres import initialize_postgres_marketplace_schema

            initialize_postgres_marketplace_schema(conn)
            return
        require_sqlite_connection(conn, "Marketplace schema initialization")
        _migrate_legacy_marketplace_table_names(conn)
        _migrate_legacy_order_event_types(conn)
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
        CREATE TABLE IF NOT EXISTS marketplace_orders (
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
        CREATE INDEX IF NOT EXISTS idx_marketplace_orders_status_created
            ON marketplace_orders(status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_marketplace_orders_user_created
            ON marketplace_orders(user_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS verified_assets (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL CHECK(source IN ('telegram_gift', 'ton_nft', 'telegram_username', 'anonymous_number')),
            external_asset_id TEXT NOT NULL,
            owner_telegram_id TEXT NOT NULL,
            title TEXT NOT NULL,
            asset_type TEXT NOT NULL CHECK(asset_type IN ('gift', 'username', 'number')),
            image_url TEXT NOT NULL DEFAULT '',
            verification_method TEXT NOT NULL CHECK(verification_method IN ('telegram_gift_owner', 'ton_connect_proof', 'telegram_username_owner', 'anonymous_number_owner')),
            verification_reference_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'verified' CHECK(status IN ('verified', 'revoked')),
            created_at INTEGER NOT NULL,
            verified_at INTEGER NOT NULL,
            revoked_at INTEGER,
            UNIQUE(source, external_asset_id)
        );
        CREATE INDEX IF NOT EXISTS idx_verified_assets_owner_status
            ON verified_assets(owner_telegram_id, status, verified_at DESC);
        CREATE TRIGGER IF NOT EXISTS verified_assets_metadata_immutable
        BEFORE UPDATE OF source, external_asset_id, owner_telegram_id, title, asset_type,
                         image_url, verification_method, verification_reference_hash, verified_at
        ON verified_assets
        BEGIN
            SELECT RAISE(ABORT, 'verified asset metadata is immutable');
        END;
        CREATE TABLE IF NOT EXISTS marketplace_order_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            actor_user_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN ('created', 'filled', 'cancelled', 'legacy_unverified')),
            created_at INTEGER NOT NULL,
            FOREIGN KEY(order_id) REFERENCES marketplace_orders(id)
        );
        CREATE INDEX IF NOT EXISTS idx_marketplace_order_events_order
            ON marketplace_order_events(order_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS marketplace_order_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            actor_user_id TEXT NOT NULL,
            changed_fields TEXT NOT NULL DEFAULT '[]',
            created_at INTEGER NOT NULL,
            FOREIGN KEY(order_id) REFERENCES marketplace_orders(id)
        );
        CREATE INDEX IF NOT EXISTS idx_marketplace_order_revisions_order
            ON marketplace_order_revisions(order_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS marketplace_order_reports (
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
            FOREIGN KEY(order_id) REFERENCES marketplace_orders(id)
        );
        CREATE INDEX IF NOT EXISTS idx_marketplace_order_reports_status_created
            ON marketplace_order_reports(status, created_at DESC);
        CREATE TABLE IF NOT EXISTS api_rate_limits (
            user_id TEXT NOT NULL,
            action TEXT NOT NULL,
            window_started_at INTEGER NOT NULL,
            request_count INTEGER NOT NULL,
            PRIMARY KEY(user_id, action)
        );
        CREATE TABLE IF NOT EXISTS security_audit_events (
            id TEXT PRIMARY KEY,
            actor_telegram_id TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN (
                'telegram_session_revoked', 'telegram_sessions_revoked_all',
                'ton_wallet_linked', 'ton_wallet_revoked',
                'admin_report_dismissed', 'admin_order_removed', 'admin_asset_revoked'
            )),
            target_type TEXT NOT NULL CHECK(target_type IN ('session', 'account', 'wallet', 'order_report', 'order', 'verified_asset')),
            target_hash TEXT NOT NULL CHECK(length(target_hash) = 64),
            outcome TEXT NOT NULL CHECK(outcome IN ('success', 'denied')),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_security_audit_actor_created
            ON security_audit_events(actor_telegram_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_security_audit_action_created
            ON security_audit_events(action, created_at DESC);
        CREATE TRIGGER IF NOT EXISTS security_audit_events_no_update
        BEFORE UPDATE ON security_audit_events
        BEGIN
            SELECT RAISE(ABORT, 'security audit events are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS security_audit_events_no_delete
        BEFORE DELETE ON security_audit_events
        BEGIN
            SELECT RAISE(ABORT, 'security audit events are immutable');
        END;
        CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS telegram_business_connections (
            telegram_id TEXT PRIMARY KEY,
            business_connection_id TEXT NOT NULL UNIQUE,
            user_chat_id TEXT NOT NULL,
            is_enabled INTEGER NOT NULL DEFAULT 0,
            can_view_gifts_and_stars INTEGER NOT NULL DEFAULT 0,
            can_transfer_and_upgrade_gifts INTEGER NOT NULL DEFAULT 0,
            can_transfer_stars INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_business_connections_enabled
            ON telegram_business_connections(telegram_id, is_enabled);
        CREATE TABLE IF NOT EXISTS telegram_business_gift_refs (
            asset_id TEXT PRIMARY KEY,
            owner_telegram_id TEXT NOT NULL,
            business_connection_id TEXT NOT NULL,
            owned_gift_id TEXT NOT NULL,
            gift_slug TEXT NOT NULL,
            model_sticker_file_id TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL,
            UNIQUE(business_connection_id, owned_gift_id),
            FOREIGN KEY(asset_id) REFERENCES verified_assets(id)
        );
        CREATE INDEX IF NOT EXISTS idx_business_gift_refs_owner
            ON telegram_business_gift_refs(owner_telegram_id, updated_at DESC);


        """)
        _migrate_security_audit_schema(conn)
        gift_ref_columns = {row["name"] for row in conn.execute("PRAGMA table_info(telegram_business_gift_refs)").fetchall()}
        if "model_sticker_file_id" not in gift_ref_columns:
            conn.execute("ALTER TABLE telegram_business_gift_refs ADD COLUMN model_sticker_file_id TEXT NOT NULL DEFAULT ''")
        # A verified ownership transfer must carry a one-use, server-created
        # authorization row. A plain SQL UPDATE must never be enough to reassign
        # an asset, even when the source and external ID remain unchanged.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS asset_owner_transfer_authorizations (
                asset_id TEXT PRIMARY KEY,
                previous_owner_id TEXT NOT NULL,
                new_owner_id TEXT NOT NULL,
                verification_reference_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(asset_id) REFERENCES verified_assets(id)
            )
        """)
        conn.execute("DROP TRIGGER IF EXISTS verified_assets_metadata_immutable")
        conn.execute("DROP TRIGGER IF EXISTS verified_assets_consume_transfer_authorization")
        conn.execute("""
            CREATE TRIGGER verified_assets_metadata_immutable
            BEFORE UPDATE OF source, external_asset_id, owner_telegram_id, title, asset_type,
                             image_url, verification_method, verification_reference_hash, verified_at
            ON verified_assets
            WHEN NOT (
                NEW.status = 'verified'
                AND NEW.owner_telegram_id != OLD.owner_telegram_id
                AND NEW.source = OLD.source
                AND NEW.external_asset_id = OLD.external_asset_id
                AND EXISTS (
                    SELECT 1 FROM asset_owner_transfer_authorizations auth
                    WHERE auth.asset_id = OLD.id
                      AND auth.previous_owner_id = OLD.owner_telegram_id
                      AND auth.new_owner_id = NEW.owner_telegram_id
                      AND auth.verification_reference_hash = NEW.verification_reference_hash
                )
            )
            BEGIN
                SELECT RAISE(ABORT, 'verified asset metadata is immutable');
            END;
        """)
        conn.execute("""
            CREATE TRIGGER verified_assets_consume_transfer_authorization
            AFTER UPDATE OF owner_telegram_id ON verified_assets
            WHEN NEW.owner_telegram_id != OLD.owner_telegram_id
            BEGIN
                DELETE FROM asset_owner_transfer_authorizations
                WHERE asset_id = NEW.id
                  AND previous_owner_id = OLD.owner_telegram_id
                  AND new_owner_id = NEW.owner_telegram_id;
            END;
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS asset_ownership_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id TEXT NOT NULL,
                previous_owner_id TEXT NOT NULL,
                new_owner_id TEXT NOT NULL,
                verification_reference_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(asset_id) REFERENCES verified_assets(id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_asset_ownership_history_asset
            ON asset_ownership_history(asset_id, created_at DESC)
        """)
        for table in ("marketplace_order_events", "marketplace_order_revisions", "asset_ownership_history"):
            conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_no_update
                BEFORE UPDATE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, 'append-only history is immutable');
                END
            """)
            conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_no_delete
                BEFORE DELETE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, 'append-only history is immutable');
                END
            """)
        # Asset category is server-owned metadata. Older rows remain unknown instead
        # of guessing the category from a seller-editable title.
        order_columns = {row["name"] for row in conn.execute("PRAGMA table_info(marketplace_orders)").fetchall()}
        if "asset_type" not in order_columns:
            conn.execute("ALTER TABLE marketplace_orders ADD COLUMN asset_type TEXT CHECK(asset_type IS NULL OR asset_type IN ('gift', 'username', 'number'))")
        if "asset_id" not in order_columns:
            conn.execute("ALTER TABLE marketplace_orders ADD COLUMN asset_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_marketplace_orders_asset ON marketplace_orders(asset_id, status)")
        # Never delete marketplace listings during application startup.
        # Any listing removal must be an explicit, owner-authorized operation with
        # a recorded audit event. Startup must preserve all user-owned rows.





def upsert_telegram_business_connection(connection: dict[str, Any]) -> dict[str, Any]:
    """Persist only the minimal owner-bound Telegram Business connection rights."""
    if not isinstance(connection, dict):
        raise ValueError("A valid Telegram Business connection is required")
    owner = connection.get("user") if isinstance(connection.get("user"), dict) else {}
    telegram_id = str(owner.get("id") or "").strip()
    connection_id = str(connection.get("id") or "").strip()
    user_chat_id = str(connection.get("user_chat_id") or "").strip()
    if not telegram_id.isdigit() or not connection_id or len(connection_id) > 256 or not user_chat_id.isdigit():
        raise ValueError("Telegram Business connection identity is invalid")
    rights = connection.get("rights") if isinstance(connection.get("rights"), dict) else {}
    enabled = connection.get("is_enabled") is True
    now = int(time.time())
    values = (
        telegram_id, connection_id, user_chat_id, 1 if enabled else 0,
        1 if rights.get("can_view_gifts_and_stars") is True else 0,
        1 if rights.get("can_transfer_and_upgrade_gifts") is True else 0,
        1 if rights.get("can_transfer_stars") is True else 0, now,
    )
    with _db() as conn:
        conn.execute(
            """INSERT INTO telegram_business_connections
               (telegram_id, business_connection_id, user_chat_id, is_enabled,
                can_view_gifts_and_stars, can_transfer_and_upgrade_gifts,
                can_transfer_stars, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(telegram_id) DO UPDATE SET
                 business_connection_id=excluded.business_connection_id,
                 user_chat_id=excluded.user_chat_id,
                 is_enabled=excluded.is_enabled,
                 can_view_gifts_and_stars=excluded.can_view_gifts_and_stars,
                 can_transfer_and_upgrade_gifts=excluded.can_transfer_and_upgrade_gifts,
                 can_transfer_stars=excluded.can_transfer_stars,
                 updated_at=excluded.updated_at""",
            values,
        )
    return get_telegram_business_connection(telegram_id) or {}



def get_telegram_business_connection_id(telegram_id: str | int) -> str | None:
    """Return a connection ID only to trusted server code after checking current rights."""
    owner_id = str(telegram_id).strip()
    if not owner_id.isdigit():
        raise ValueError("A verified Telegram owner ID is required")
    with _db() as conn:
        row = conn.execute(
            """SELECT business_connection_id FROM telegram_business_connections
               WHERE telegram_id = ? AND is_enabled = 1
                 AND can_view_gifts_and_stars = 1""",
            (owner_id,),
        ).fetchone()
    return str(row["business_connection_id"]) if row else None


def save_telegram_business_gift_ref(
    *, asset_id: str, owner_telegram_id: str, business_connection_id: str,
    owned_gift_id: str, gift_slug: str, model_sticker_file_id: str = "",
) -> None:
    """Bind the Telegram-issued owned_gift_id to a verified marketplace asset."""
    owner_id = str(owner_telegram_id).strip()
    if not owner_id.isdigit() or not all((asset_id, business_connection_id, owned_gift_id, gift_slug)):
        raise ValueError("A complete verified Business gift reference is required")
    now = int(time.time())
    with _db() as conn:
        conn.execute(
            """INSERT INTO telegram_business_gift_refs
               (asset_id, owner_telegram_id, business_connection_id, owned_gift_id, gift_slug, model_sticker_file_id, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(asset_id) DO UPDATE SET
                 owner_telegram_id=excluded.owner_telegram_id,
                 business_connection_id=excluded.business_connection_id,
                 owned_gift_id=excluded.owned_gift_id,
                 gift_slug=excluded.gift_slug,
                 model_sticker_file_id=excluded.model_sticker_file_id,
                 updated_at=excluded.updated_at""",
            (asset_id, owner_id, business_connection_id, owned_gift_id, gift_slug, str(model_sticker_file_id or "")[:512], now),
        )



def prune_telegram_business_gift_refs(
    *, owner_telegram_id: str, business_connection_id: str, active_owned_gift_ids: set[str],
) -> None:
    """Remove stale transfer handles only after a complete provider snapshot."""
    owner_id = str(owner_telegram_id).strip()
    if not owner_id.isdigit() or not business_connection_id:
        raise ValueError("A verified Business account is required")
    with _db() as conn:
        if active_owned_gift_ids:
            placeholders = ",".join("?" for _ in active_owned_gift_ids)
            conn.execute(
                f"""DELETE FROM telegram_business_gift_refs
                    WHERE owner_telegram_id = ? AND business_connection_id = ?
                      AND owned_gift_id NOT IN ({placeholders})""",
                (owner_id, business_connection_id, *sorted(active_owned_gift_ids)),
            )
        else:
            conn.execute(
                """DELETE FROM telegram_business_gift_refs
                   WHERE owner_telegram_id = ? AND business_connection_id = ?""",
                (owner_id, business_connection_id),
            )


def get_telegram_business_gift_preview_ref(asset_id: str, owner_telegram_id: str | int) -> dict[str, Any] | None:
    """Return a preview handle only to the current owner with view-gifts permission."""
    owner_id = str(owner_telegram_id).strip()
    if not owner_id.isdigit():
        raise ValueError("A verified Telegram owner ID is required")
    with _db() as conn:
        row = conn.execute(
            """SELECT r.model_sticker_file_id
               FROM telegram_business_gift_refs r
               JOIN telegram_business_connections c
                 ON c.telegram_id = r.owner_telegram_id
                AND c.business_connection_id = r.business_connection_id
               JOIN verified_assets a ON a.id = r.asset_id
               WHERE r.asset_id = ? AND r.owner_telegram_id = ?
                 AND c.is_enabled = 1 AND c.can_view_gifts_and_stars = 1
                 AND a.owner_telegram_id = ? AND a.status = 'verified'""",
            (str(asset_id), owner_id, owner_id),
        ).fetchone()
    return dict(row) if row else None


def get_telegram_business_gift_ref(asset_id: str, owner_telegram_id: str | int) -> dict[str, Any] | None:
    owner_id = str(owner_telegram_id).strip()
    if not owner_id.isdigit():
        raise ValueError("A verified Telegram owner ID is required")
    with _db() as conn:
        row = conn.execute(
            """SELECT r.asset_id, r.owned_gift_id, r.gift_slug, r.business_connection_id
               FROM telegram_business_gift_refs r
               JOIN telegram_business_connections c
                 ON c.telegram_id = r.owner_telegram_id
                AND c.business_connection_id = r.business_connection_id
               JOIN verified_assets a ON a.id = r.asset_id
               WHERE r.asset_id = ? AND r.owner_telegram_id = ?
                 AND c.is_enabled = 1 AND c.can_transfer_and_upgrade_gifts = 1
                 AND a.owner_telegram_id = ? AND a.status = 'verified'""",
            (str(asset_id), owner_id, owner_id),
        ).fetchone()
    return dict(row) if row else None

def get_telegram_business_connection(telegram_id: str | int) -> dict[str, Any] | None:
    owner_id = str(telegram_id).strip()
    if not owner_id.isdigit():
        raise ValueError("A verified Telegram owner ID is required")
    with _db() as conn:
        row = conn.execute(
            """SELECT telegram_id, is_enabled, can_view_gifts_and_stars,
                      can_transfer_and_upgrade_gifts, can_transfer_stars, updated_at
               FROM telegram_business_connections WHERE telegram_id = ?""",
            (owner_id,),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["connected"] = bool(result.pop("is_enabled"))
    result["can_view_gifts_and_stars"] = bool(result["can_view_gifts_and_stars"])
    result["can_transfer_and_upgrade_gifts"] = bool(result["can_transfer_and_upgrade_gifts"])
    result["can_transfer_stars"] = bool(result["can_transfer_stars"])
    result["gift_import_ready"] = result["connected"] and result["can_view_gifts_and_stars"]
    result["gift_transfer_ready"] = (
        result["connected"] and result["can_view_gifts_and_stars"]
        and result["can_transfer_and_upgrade_gifts"]
    )
    return result

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
    values = (telegram_id, str(user.get("username") or "")[:64], str(user.get("first_name") or "")[:128], str(user.get("last_name") or "")[:128], str(user.get("photo_url") or "")[:2048], str(user.get("language_code") or "")[:16], bool(user.get("is_premium") is True), now, now)
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
        begin_write(conn, f"telegram-initdata:{digest}")
        conn.execute("DELETE FROM telegram_initdata_uses WHERE expires_at <= ?", (now,))
        try:
            conn.execute(
                "INSERT INTO telegram_initdata_uses(initdata_hash, telegram_id, consumed_at, expires_at) VALUES (?, ?, ?, ?)",
                (digest, str(telegram_id), now, now + max(300, min(int(ttl_seconds), 3600))),
            )
        except Exception as error:
            if not is_integrity_error(error):
                raise
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
    now = int(time.time())
    with _db() as conn:
        begin_write(conn, f"session-revoke:{token_hash}")
        session = conn.execute(
            "SELECT telegram_id FROM telegram_sessions WHERE token_hash = ? AND revoked_at IS NULL",
            (token_hash,),
        ).fetchone()
        if session is None:
            conn.rollback()
            return False
        cursor = conn.execute(
            "UPDATE telegram_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (now, token_hash),
        )
        if cursor.rowcount == 1:
            insert_security_audit_event(
                conn,
                actor_telegram_id=str(session["telegram_id"]),
                action="telegram_session_revoked",
                target_type="session",
                target_id=token_hash,
                metadata={"reason": "self_logout"},
                now=now,
            )
        conn.commit()
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
        begin_write(conn, f"session-revoke-all:{telegram_id}")
        cursor = conn.execute(
            f"""UPDATE telegram_sessions SET revoked_at = ?
                WHERE telegram_id = ? AND revoked_at IS NULL AND expires_at > ?{exclusion}""",
            tuple(params),
        )
        if cursor.rowcount:
            insert_security_audit_event(
                conn,
                actor_telegram_id=str(telegram_id),
                action="telegram_sessions_revoked_all",
                target_type="account",
                target_id=str(telegram_id),
                metadata={"revoked_count": int(cursor.rowcount)},
                now=now,
            )
        conn.commit()
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
        begin_write(conn, f"telegram-update:{update_id}")
        conn.execute(
            "DELETE FROM telegram_update_inbox WHERE status = 'processed' AND processed_at < ?",
            (now - 1_209_600,),
        )
        row = conn.execute(
            select_for_update(conn, "SELECT payload_hash, status, lease_until FROM telegram_update_inbox WHERE update_id = ?"),
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


def _is_isolated_test_runtime() -> bool:
    """Return true only for an explicitly marked test runtime outside Railway production."""
    app_env = os.getenv("APP_ENV", "").strip().lower()
    railway_env = os.getenv("RAILWAY_ENVIRONMENT_NAME", "").strip().lower()
    return app_env in {"test", "testing"} and railway_env != "production"


def _test_unverified_assets_allowed() -> bool:
    """Allow legacy unverified fixtures only in an explicitly isolated test runtime."""
    return (
        _is_isolated_test_runtime()
        and os.getenv("HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS", "false").strip().lower() == "true"
    )


def _test_ton_owner_recheck_skipped() -> bool:
    """Allow skipping live TON owner checks only in isolated tests, never production."""
    return (
        _is_isolated_test_runtime()
        and os.getenv("HASSLE_TEST_SKIP_TON_OWNER_RECHECK", "false").strip().lower() == "true"
    )


def register_verified_asset(*, source: str, external_asset_id: str, owner_telegram_id: str,
                            title: str, asset_type: str, image_url: str,
                            verification_method: str, verification_reference: str,
                            allow_owner_transfer: bool = False) -> dict[str, Any]:
    """Persist an asset only after a trusted upstream verifier has validated ownership.

    This function is intentionally not an HTTP endpoint. A TON Connect / Telegram
    verifier must validate the proof before calling it. Client claims are not proof.
    """
    allowed = {
        ("telegram_gift", "gift", "telegram_gift_owner"),
        ("ton_nft", "gift", "ton_connect_proof"),
        ("ton_nft", "username", "ton_connect_proof"),
        ("ton_nft", "number", "ton_connect_proof"),
        ("telegram_username", "username", "telegram_username_owner"),
        ("anonymous_number", "number", "anonymous_number_owner"),
    }
    source, asset_type, verification_method = str(source), str(asset_type), str(verification_method)
    external_asset_id = str(external_asset_id).strip()
    owner_telegram_id = str(owner_telegram_id).strip()
    title = str(title).strip()
    image_url = str(image_url or "").strip()
    verification_reference = str(verification_reference or "").strip()
    if (source, asset_type, verification_method) not in allowed:
        raise ValueError("Asset source, type and verification method do not match")
    if not external_asset_id or len(external_asset_id) > 200:
        raise ValueError("A valid external asset ID is required")
    if not owner_telegram_id.isdigit():
        raise ValueError("A verified Telegram owner ID is required")
    if not title or len(title) > 120:
        raise ValueError("A valid verified asset title is required")
    if image_url:
        parsed = urlparse(image_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or len(image_url) > 2048:
            raise ValueError("Asset images must use a valid HTTPS URL")
    if len(verification_reference) < 16 or len(verification_reference) > 4096:
        raise ValueError("A valid one-time verification reference is required")
    now = int(time.time())
    reference_hash = hashlib.sha256(verification_reference.encode("utf-8")).hexdigest()
    with _db() as conn:
        begin_write(conn, f"verified-asset:{source}:{external_asset_id}")
        existing = conn.execute(
            select_for_update(conn, "SELECT * FROM verified_assets WHERE source = ? AND external_asset_id = ?"),
            (source, external_asset_id),
        ).fetchone()
        if existing is not None:
            if existing["owner_telegram_id"] != owner_telegram_id:
                if not allow_owner_transfer:
                    conn.rollback()
                    raise ValueError("This asset is already registered to another owner")
                previous_owner_id = existing["owner_telegram_id"]
                # The DB trigger accepts this update only when this exact
                # asset/old-owner/new-owner/hash tuple was authorized in the
                # current transaction. The AFTER trigger consumes the row.
                conn.execute(
                    """INSERT INTO asset_owner_transfer_authorizations
                       (asset_id, previous_owner_id, new_owner_id, verification_reference_hash, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (existing["id"], previous_owner_id, owner_telegram_id, reference_hash, now),
                )
                changed = conn.execute(
                    """UPDATE verified_assets
                       SET owner_telegram_id = ?, title = ?, asset_type = ?, image_url = ?,
                           verification_method = ?, verification_reference_hash = ?,
                           verified_at = ?, status = 'verified', revoked_at = NULL
                       WHERE id = ? AND status IN ('verified', 'revoked')""",
                    (owner_telegram_id, title, asset_type, image_url, verification_method,
                     reference_hash, now, existing["id"]),
                )
                if changed.rowcount != 1:
                    conn.rollback()
                    raise ValueError("Asset ownership changed during verification")
                conn.execute(
                    """INSERT INTO asset_ownership_history
                       (asset_id, previous_owner_id, new_owner_id, verification_reference_hash, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (existing["id"], previous_owner_id, owner_telegram_id, reference_hash, now),
                )
                # A listing from the previous owner must never become visible again.
                stale_listings = conn.execute(
                    "SELECT id FROM marketplace_orders WHERE asset_id = ? AND status = 'active'",
                    (existing["id"],),
                ).fetchall()
                stale_listing_ids = [row["id"] for row in stale_listings]
                for listing in stale_listings:
                    conn.execute(
                        "UPDATE marketplace_orders SET status = 'cancelled', updated_at = ? WHERE id = ?",
                        (now, listing["id"]),
                    )
                    conn.execute(
                        """INSERT INTO marketplace_order_events(order_id, actor_user_id, event_type, created_at)
                           VALUES (?, ?, 'cancelled', ?)""",
                        (listing["id"], owner_telegram_id, now),
                    )
                if stale_listing_ids:
                    placeholders = ",".join("?" for _ in stale_listing_ids)
                    conn.execute(
                        f"""UPDATE marketplace_offers SET status = 'declined', updated_at = ?
                            WHERE order_id IN ({placeholders}) AND status IN ('pending', 'accepted')""",
                        (now, *stale_listing_ids),
                    )
                    conn.execute(
                        f"""UPDATE marketplace_deals SET status = 'cancelled'
                            WHERE order_id IN ({placeholders})
                              AND status NOT IN ('cancelled', 'expired', 'completed')""",
                        tuple(stale_listing_ids),
                    )
                conn.commit()
                existing = conn.execute(
                    "SELECT * FROM verified_assets WHERE id = ?", (existing["id"],)
                ).fetchone()
            elif existing["status"] != "verified":
                conn.execute(
                    "UPDATE verified_assets SET status = 'verified', revoked_at = NULL WHERE id = ?",
                    (existing["id"],),
                )
                conn.commit()
                existing = conn.execute(
                    "SELECT * FROM verified_assets WHERE id = ?", (existing["id"],)
                ).fetchone()
            else:
                conn.rollback()
            return {"id": existing["id"], "source": existing["source"],
                    "external_asset_id": existing["external_asset_id"],
                    "owner_telegram_id": existing["owner_telegram_id"],
                    "title": existing["title"], "asset_type": existing["asset_type"],
                    "image_url": existing["image_url"], "status": existing["status"],
                    "verified_at": existing["verified_at"]}
        asset_id = "ast_" + uuid.uuid4().hex
        conn.execute(
            """INSERT INTO verified_assets
               (id, source, external_asset_id, owner_telegram_id, title, asset_type,
                image_url, verification_method, verification_reference_hash,
                status, created_at, verified_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'verified', ?, ?)""",
            (asset_id, source, external_asset_id, owner_telegram_id, title, asset_type,
             image_url, verification_method, reference_hash, now, now),
        )
        conn.commit()
    return {"id": asset_id, "source": source, "external_asset_id": external_asset_id,
            "owner_telegram_id": owner_telegram_id, "title": title, "asset_type": asset_type,
            "image_url": image_url, "status": "verified", "verified_at": now}


def get_verified_asset_owner(source: str, external_asset_id: str) -> str | None:
    """Return the current registry owner for one exact upstream asset identity."""
    safe_source = str(source or "").strip()
    safe_external_id = str(external_asset_id or "").strip()
    if safe_source not in {"ton_nft", "telegram_gift", "telegram_username", "anonymous_number"}:
        raise ValueError("Unsupported verified asset source")
    if not safe_external_id or len(safe_external_id) > 200:
        raise ValueError("A valid external asset ID is required")
    with _db() as conn:
        row = conn.execute(
            "SELECT owner_telegram_id FROM verified_assets WHERE source = ? AND external_asset_id = ?",
            (safe_source, safe_external_id),
        ).fetchone()
    return str(row["owner_telegram_id"]) if row else None


def sync_ton_nft_assets(*, owner_telegram_id: str, wallets: list[dict[str, Any]],
                          nft_items: list[dict[str, str]], complete: bool) -> list[dict[str, Any]]:
    """Persist provider-derived NFT snapshots and revoke stale ownership only on complete scans."""
    owner_id = str(owner_telegram_id).strip()
    if not owner_id.isdigit():
        raise ValueError("A verified Telegram owner ID is required")
    current_ids = {str(item.get("address") or "").strip() for item in nft_items}
    current_ids.discard("")
    registered: list[dict[str, Any]] = []
    wallet_addresses = {str(wallet.get("address") or "") for wallet in wallets}
    for item in nft_items:
        address = str(item.get("address") or "").strip()
        evidence_wallet = str(item.get("wallet_address") or "").strip()
        if not address or evidence_wallet not in wallet_addresses:
            continue
        # Wallet proof binds the Telegram account to this address; TonAPI's
        # direct-owner NFT response supplies the current on-chain owner assertion.
        asset_type = str(item.get("asset_type") or "gift")
        if asset_type not in {"gift", "username", "number"}:
            asset_type = "gift"
        verification_reference = str(item.get("verification_reference") or "")
        if asset_type in {"username", "number"} and not verification_reference.startswith("ton-contract-v1:"):
            raise ValueError("Fragment assets require a successful TON contract verification")
        if not verification_reference:
            verification_reference = f"tonapi-owner-index:{evidence_wallet}:{address}:{int(time.time())}"
        registered.append(register_verified_asset(
            source="ton_nft",
            external_asset_id=address,
            owner_telegram_id=owner_id,
            title=str(item.get("title") or "TON NFT")[:120],
            asset_type=asset_type,
            image_url=str(item.get("image_url") or ""),
            verification_method="ton_connect_proof",
            verification_reference=verification_reference,
            allow_owner_transfer=True,
        ))
    if complete:
        now = int(time.time())
        with _db() as conn:
            if current_ids:
                placeholders = ",".join("?" for _ in current_ids)
                conn.execute(
                    f"""UPDATE verified_assets SET status = 'revoked', revoked_at = ?
                        WHERE owner_telegram_id = ? AND source = 'ton_nft'
                          AND status = 'verified' AND external_asset_id NOT IN ({placeholders})""",
                    (now, owner_id, *sorted(current_ids)),
                )
            else:
                conn.execute(
                    """UPDATE verified_assets SET status = 'revoked', revoked_at = ?
                       WHERE owner_telegram_id = ? AND source = 'ton_nft' AND status = 'verified'""",
                    (now, owner_id),
                )
    return registered



def sync_telegram_gift_assets(*, owner_telegram_id: str,
                              gifts: list[dict[str, str]], complete: bool) -> list[dict[str, Any]]:
    """Persist unique gifts returned by Telegram for the authenticated Telegram user."""
    owner_id = str(owner_telegram_id).strip()
    if not owner_id.isdigit():
        raise ValueError("A verified Telegram owner ID is required")
    current_ids = {
        str(item.get("external_asset_id") or "").strip()
        for item in gifts
        if str(item.get("external_asset_id") or "").strip()
    }
    registered: list[dict[str, Any]] = []
    for item in gifts:
        external_id = str(item.get("external_asset_id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not external_id or not title:
            continue
        # The official Bot API response is scoped to the ID from verified WebApp initData.
        # Only unique gifts have a stable item identifier; regular gifts are not guessed.
        registered.append(register_verified_asset(
            source="telegram_gift",
            external_asset_id=external_id,
            owner_telegram_id=owner_id,
            title=title[:120],
            asset_type="gift",
            image_url=str(item.get("image_url") or ""),
            verification_method="telegram_gift_owner",
            verification_reference=(
                f"telegram-bot-api:getUserGifts:{owner_id}:{external_id}:"
                f"{str(item.get('send_date') or '')}:{int(time.time())}"
            ),
            allow_owner_transfer=True,
        ))
    if complete:
        now = int(time.time())
        with _db() as conn:
            if current_ids:
                placeholders = ",".join("?" for _ in current_ids)
                conn.execute(
                    f"""UPDATE verified_assets SET status = 'revoked', revoked_at = ?
                        WHERE owner_telegram_id = ? AND source = 'telegram_gift'
                          AND status = 'verified' AND external_asset_id NOT IN ({placeholders})""",
                    (now, owner_id, *sorted(current_ids)),
                )
            else:
                conn.execute(
                    """UPDATE verified_assets SET status = 'revoked', revoked_at = ?
                       WHERE owner_telegram_id = ? AND source = 'telegram_gift' AND status = 'verified'""",
                    (now, owner_id),
                )
    return registered


def _verified_asset_actions_enabled() -> bool:
    """Listing mutations stay closed until TON Connect ownership verification is live."""
    return os.getenv("HASSLE_VERIFIED_ASSETS_ENABLED", "false").strip().lower() == "true"


def _user(init_data: str | None, verify: Callable[[str], dict]) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="Open HASSLE NFT from Telegram to manage marketplace listings")
    return verify(init_data)


_REDIS_RATE_LIMIT_SCRIPT = """
local current = redis.call("INCR", KEYS[1])
if current == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
local ttl = redis.call("TTL", KEYS[1])
return {current, ttl}
"""
_redis_rate_limit_client = None
_redis_rate_limit_url = None


def _get_redis_rate_limit_client():
    """Return a process-wide Redis client when REDIS_URL is configured."""
    global _redis_rate_limit_client, _redis_rate_limit_url
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        return None
    if _redis_rate_limit_client is None or _redis_rate_limit_url != redis_url:
        from redis import Redis
        _redis_rate_limit_client = Redis.from_url(
            redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            health_check_interval=30,
            decode_responses=True,
        )
        _redis_rate_limit_url = redis_url
    return _redis_rate_limit_client


def _enforce_sqlite_user_rate_limit(user_id: str, action: str, limit: int, window_seconds: int) -> None:
    """Persistent per-Telegram-user limit using the configured database backend."""
    now = int(time.time())
    with _db() as conn:
        begin_write(conn, f"rate-limit:{user_id}:{action}")
        row = conn.execute(
            select_for_update(conn, "SELECT window_started_at, request_count FROM api_rate_limits WHERE user_id = ? AND action = ?"),
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


def _enforce_user_rate_limit(user_id: str, action: str, limit: int, window_seconds: int = 60) -> None:
    """Enforce a fixed-window limit with Redis and a persistent DB fallback.

    Redis uses one atomic Lua operation for INCR + EXPIRE so a crash cannot leave
    a permanent counter without a TTL. If Redis is unavailable, the configured
    database remains authoritative. Production PostgreSQL shares fallback
    counters across replicas; SQLite is only the local/test backend.
    """
    client = _get_redis_rate_limit_client()
    if client is not None:
        from redis.exceptions import RedisError
        key_digest = hashlib.sha256(f"{user_id}|{action}".encode("utf-8")).hexdigest()
        key = f"hassle:rate-limit:{key_digest}"
        try:
            count, ttl = client.eval(_REDIS_RATE_LIMIT_SCRIPT, 1, key, max(1, window_seconds))
            if int(count) > limit:
                retry_after = max(1, int(ttl) if int(ttl) > 0 else window_seconds)
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests. Please wait before trying again.",
                    headers={"Retry-After": str(retry_after)},
                )
            return
        except HTTPException:
            raise
        except RedisError:
            # Preserve rate limiting during a Redis outage using the configured DB.
            pass
    _enforce_sqlite_user_rate_limit(user_id, action, limit, window_seconds)

def _order_dict(row: sqlite3.Row, current_user_id: str | None = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "asset_id": row["asset_id"] if "asset_id" in row.keys() else None,
        "image_url": row["asset_image_url"] if "asset_image_url" in row.keys() else "",
        "asset_type": row["asset_type"] if "asset_type" in row.keys() else None,
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
    asset_id: str | None = Field(default=None, min_length=8, max_length=80)
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

    def require_admin_role(admin: dict, allowed_roles: set[str]) -> None:
        role = admin.get("admin_role", "owner")
        if role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Administrator role does not grant this permission")

    @router.get("/assets/me")
    async def list_my_verified_assets(
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        owner_id = str(user["id"])
        _enforce_user_rate_limit(owner_id, "list_verified_assets", 60)
        with _db() as conn:
            rows = conn.execute(
                """SELECT a.id, a.source, a.external_asset_id, a.title, a.asset_type,
                          a.image_url, a.verified_at,
                          (SELECT o.id FROM marketplace_orders o
                           WHERE o.asset_id = a.id AND o.status = 'active'
                           ORDER BY o.created_at DESC LIMIT 1) AS active_listing_id
                   FROM verified_assets a
                   WHERE a.owner_telegram_id = ? AND a.status = 'verified'
                   ORDER BY a.verified_at DESC LIMIT 200""",
                (owner_id,),
            ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": len(rows),
            "listing_enabled": _verified_asset_actions_enabled(),
        }

    @router.get("/profiles/me")
    async def get_my_seller_profile(
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        telegram_id = str(user["id"])
        _enforce_user_rate_limit(telegram_id, "get_seller_profile", 30)
        now = int(time.time())
        fallback_name = (user.get("first_name") or user.get("username") or "Telegram user")[:48]
        with _db() as conn:
            conn.execute(
                """INSERT INTO user_profiles
                   (profile_id, telegram_id, display_name, bio, created_at, updated_at)
                   VALUES (?, ?, ?, '', ?, ?)
                   ON CONFLICT(telegram_id) DO NOTHING""",
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
            begin_write(conn, f"seller-profile:{telegram_id}")
            conn.execute(
                """INSERT INTO user_profiles
                   (profile_id, telegram_id, display_name, bio, created_at, updated_at)
                   VALUES (?, ?, ?, '', ?, ?)
                   ON CONFLICT(telegram_id) DO NOTHING""",
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
                          (SELECT COUNT(*) FROM marketplace_orders o
                           JOIN verified_assets a ON a.id = o.asset_id
                             AND a.owner_telegram_id = o.user_id AND a.status = 'verified'
                           WHERE o.user_id = p.telegram_id AND o.kind = 'listing'
                             AND o.status = 'active') AS active_listings
                   FROM user_profiles p JOIN telegram_users u ON u.telegram_id = p.telegram_id
                   WHERE p.profile_id = ?""",
                (profile_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Seller profile not found")
            if not _verified_asset_actions_enabled():
                return {"profile": {**dict(row), "active_listings": 0, "completed_trades": 0, "rating": None, "listings": []}}
            listing_rows = conn.execute(
                """SELECT o.id, o.title, o.price, o.currency, o.created_at, o.note,
                          a.image_url AS image_url, a.asset_type AS asset_type
                   FROM marketplace_orders o
                   JOIN user_profiles p ON p.telegram_id = o.user_id
                   JOIN verified_assets a ON a.id = o.asset_id
                     AND a.owner_telegram_id = o.user_id AND a.status = 'verified'
                   WHERE p.profile_id = ? AND o.kind = 'listing' AND o.status = 'active'
                   ORDER BY o.created_at DESC LIMIT 100""",
                (profile_id,),
            ).fetchall()
        # Public projection is allow-listed: no Telegram numeric ID, session data,
        # buyer requests, private fields or unverified reputation.
        public_listings = [{
            "id": item["id"], "title": item["title"], "price": item["price"],
            "currency": item["currency"], "createdAt": item["created_at"], "note": item["note"],
            "image_url": item["image_url"], "asset_type": item["asset_type"],
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
            begin_write(conn, f"order:{order_id}", f"order-report:{order_id}:{reporter_id}")
            order = conn.execute(select_for_update(conn, "SELECT * FROM marketplace_orders WHERE id = ?"), (order_id,)).fetchone()
            if order is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Marketplace order not found")
            if order["user_id"] == reporter_id:
                conn.rollback()
                raise HTTPException(status_code=403, detail="You cannot report your own order")
            if order["status"] != "active":
                conn.rollback()
                raise HTTPException(status_code=409, detail="Only active orders can be reported")
            duplicate = conn.execute(
                "SELECT 1 FROM marketplace_order_reports WHERE order_id = ? AND reporter_user_id = ?",
                (order_id, reporter_id),
            ).fetchone()
            if duplicate:
                conn.rollback()
                raise HTTPException(status_code=409, detail="You have already reported this order")
            report_count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_order_reports WHERE order_id = ? AND status = 'open'",
                (order_id,),
            ).fetchone()[0]
            if report_count >= 25:
                conn.rollback()
                raise HTTPException(status_code=409, detail="This order has reached its report limit")
            report_id = "rp-" + uuid.uuid4().hex[:16]
            reporter_name = (user.get("first_name") or user.get("username") or "Telegram user")[:64]
            conn.execute(
                """INSERT INTO marketplace_order_reports
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
        admin = verify_admin(x_telegram_init_data)
        require_admin_role(admin, {"owner", "moderator", "support"})
        _enforce_user_rate_limit(str(admin["id"]), "list_order_reports", 60)
        where = ""
        values: list[object] = []
        if status != "all":
            where = " WHERE r.status = ?"
            values.append(status)
        with _db() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM marketplace_order_reports r{where}", values,
            ).fetchone()[0]
            rows = conn.execute(
                f"""SELECT r.id, r.order_id, r.reporter_name, r.reason, r.details,
                           r.status, r.created_at, r.resolved_at, o.title AS order_title,
                           o.status AS order_status, o.owner_name AS order_owner
                    FROM marketplace_order_reports r JOIN marketplace_orders o ON o.id = r.order_id
                    {where} ORDER BY r.created_at DESC, r.id DESC LIMIT ? OFFSET ?""",
                (*values, limit, offset),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    @router.get("/admin/security-audit")
    async def list_security_audit_events(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=10000),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        if verify_admin is None:
            raise HTTPException(status_code=503, detail="Admin audit is not configured")
        admin = verify_admin(x_telegram_init_data)
        require_admin_role(admin, {"owner", "finance"})
        _enforce_user_rate_limit(str(admin["id"]), "list_security_audit", 30)
        with _db() as conn:
            rows = conn.execute(
                """SELECT id, actor_telegram_id, action, target_type, target_hash,
                          outcome, metadata_json, created_at
                   FROM security_audit_events
                   ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM security_audit_events").fetchone()[0]
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.pop("metadata_json"))
            except (TypeError, ValueError):
                item["metadata"] = {}
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @router.patch("/admin/reports/{report_id}")
    async def moderate_order_report(
        report_id: str,
        payload: ModerateOrderReport,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        if verify_admin is None:
            raise HTTPException(status_code=503, detail="Admin moderation is not configured")
        admin = verify_admin(x_telegram_init_data)
        require_admin_role(admin, {"owner", "moderator"})
        admin_id = str(admin["id"])
        _enforce_user_rate_limit(admin_id, "moderate_order_report", 30)
        now = int(time.time())
        with _db() as conn:
            begin_write(conn, f"order-report-id:{report_id}")
            report = conn.execute(
                select_for_update(conn, """SELECT r.*, o.status AS order_status FROM marketplace_order_reports r
                   JOIN marketplace_orders o ON o.id = r.order_id WHERE r.id = ?"""),
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
                    "UPDATE marketplace_orders SET status='cancelled', updated_at=? WHERE id=? AND status='active'",
                    (now, report["order_id"]),
                )
                conn.execute(
                    "INSERT INTO marketplace_order_events(order_id, actor_user_id, event_type, created_at) VALUES (?, ?, 'cancelled', ?)",
                    (report["order_id"], admin_id, now),
                )
                new_status = "removed"
            else:
                new_status = "dismissed"
            conn.execute(
                "UPDATE marketplace_order_reports SET status=?, resolved_at=?, resolved_by=? WHERE id=? AND status='open'",
                (new_status, now, admin_id, report_id),
            )
            if payload.action == "remove_order":
                insert_security_audit_event(
                    conn,
                    actor_telegram_id=admin_id,
                    action="admin_order_removed",
                    target_type="order",
                    target_id=str(report["order_id"]),
                    metadata={"report_id": report_id},
                    now=now,
                )
            else:
                insert_security_audit_event(
                    conn,
                    actor_telegram_id=admin_id,
                    action="admin_report_dismissed",
                    target_type="order_report",
                    target_id=report_id,
                    metadata={"order_id": str(report["order_id"])},
                    now=now,
                )
            conn.commit()
        return {"report_id": report_id, "status": new_status, "order_removed": payload.action == "remove_order"}

    @router.get("/favorites")
    async def list_favorites(x_telegram_init_data: str | None = Header(default=None)):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        _enforce_user_rate_limit(str(user["id"]), "list_favorites", 60)
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
            begin_write(conn, f"user-favorites:{user['id']}")
            exists = conn.execute(
                select_for_update(conn, "SELECT 1 FROM user_favorites WHERE telegram_id = ? AND gift_id = ?"),
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
        verified_actions_enabled = _verified_asset_actions_enabled()
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
        if not verified_actions_enabled:
            clauses.append("kind = ?")
            values.append("buy-order")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with _db() as conn:
            qualified_where = where.replace("status", "o.status").replace("kind", "o.kind")
            asset_filter = "" if _test_unverified_assets_allowed() else " AND (o.kind != 'listing' OR a.id IS NOT NULL)"
            if asset_filter and not qualified_where:
                qualified_where = " WHERE (o.kind != 'listing' OR a.id IS NOT NULL)"
            elif asset_filter:
                qualified_where += asset_filter
            rows = conn.execute(
                f"""SELECT o.*, p.profile_id AS seller_profile_id, a.image_url AS asset_image_url
                    FROM marketplace_orders o
                    LEFT JOIN user_profiles p ON p.telegram_id = o.user_id
                    LEFT JOIN verified_assets a ON a.id = o.asset_id
                       AND a.owner_telegram_id = o.user_id AND a.status = 'verified'
                    {qualified_where}
                    ORDER BY o.created_at DESC LIMIT ? OFFSET ?""",
                (*values, limit, offset),
            ).fetchall()
            count_where = where.replace("status", "o.status").replace("kind", "o.kind")
            if asset_filter and not count_where:
                count_where = " WHERE (o.kind != 'listing' OR a.id IS NOT NULL)"
            elif asset_filter:
                count_where += asset_filter
            total = conn.execute(
                f"""SELECT COUNT(*) FROM marketplace_orders o
                    LEFT JOIN verified_assets a ON a.id = o.asset_id
                       AND a.owner_telegram_id = o.user_id AND a.status = 'verified'
                    {count_where}""", values,
            ).fetchone()[0]
        return {"items": [_order_dict(row, user_id) for row in rows], "total": total}

    @router.get("/mine")
    async def my_orders(x_telegram_init_data: str | None = Header(default=None)):
        verified_actions_enabled = _verified_asset_actions_enabled()
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        _enforce_user_rate_limit(str(user["id"]), "list_my_orders", 60)
        with _db() as conn:
            verified_filter = "" if _test_unverified_assets_allowed() else " AND (o.kind != 'listing' OR a.id IS NOT NULL)"
            if not verified_actions_enabled:
                verified_filter += " AND o.kind = 'buy-order'"
            rows = conn.execute(
                f"""SELECT o.*, p.profile_id AS seller_profile_id, a.image_url AS asset_image_url
                    FROM marketplace_orders o
                    LEFT JOIN user_profiles p ON p.telegram_id = o.user_id
                    LEFT JOIN verified_assets a ON a.id = o.asset_id
                       AND a.owner_telegram_id = o.user_id AND a.status = 'verified'
                    WHERE o.user_id = ?{verified_filter}
                    ORDER BY o.created_at DESC LIMIT 200""",
                (str(user["id"]),),
            ).fetchall()
        return {"items": [_order_dict(row, str(user["id"])) for row in rows]}

    @router.get("/stats")
    async def marketplace_stats():
        if not _verified_asset_actions_enabled():
            with _db() as conn:
                active_buy_requests = conn.execute(
                    "SELECT COUNT(*) FROM marketplace_orders WHERE kind='buy-order' AND status='active'"
                ).fetchone()[0]
            return {
                "active_listings": 0, "active_buy_requests": active_buy_requests,
                "matched_orders": 0, "completed_trades": 0, "volume_ton": 0, "volume_stars": 0,
                "floor_ton": None, "floor_stars": None, "total_orders": active_buy_requests,
                "volume_ton_24h": 0, "volume_stars_24h": 0, "completed_trades_24h": 0,
                "collections": [],
            }

        since_24h = int(time.time()) - 86400
        with _db() as conn:
            counts = conn.execute("""
                SELECT
                    SUM(CASE WHEN status='active' AND kind='listing' THEN 1 ELSE 0 END) AS active_listings,
                    SUM(CASE WHEN status='active' AND kind='buy-order' THEN 1 ELSE 0 END) AS active_buy_requests,
                    SUM(CASE WHEN status='matched' THEN 1 ELSE 0 END) AS matched_orders,
                    MIN(CASE WHEN status='active' AND kind='listing' AND currency='TON' THEN CAST(price AS REAL) END) AS floor_ton,
                    MIN(CASE WHEN status='active' AND kind='listing' AND currency='XTR' THEN CAST(price AS REAL) END) AS floor_stars,
                    COUNT(*) AS total_orders
                FROM marketplace_orders
            """).fetchone()
            active_rows = conn.execute("""
                SELECT o.title, COALESCE(o.asset_type, a.asset_type) AS asset_type,
                       o.currency, CAST(o.price AS REAL) AS price
                FROM marketplace_orders o
                LEFT JOIN verified_assets a ON a.id = o.asset_id
                WHERE o.status='active' AND o.kind='listing'
            """).fetchall()
            completed_rows = conn.execute("""
                SELECT d.id, d.agreed_amount, d.currency, e.created_at,
                       o.title, COALESCE(o.asset_type, a.asset_type) AS asset_type
                FROM marketplace_escrow_events e
                JOIN marketplace_deals d ON d.id = e.deal_id
                JOIN marketplace_orders o ON o.id = d.order_id
                LEFT JOIN verified_assets a ON a.id = o.asset_id
                WHERE e.event_type='deal_completed'
            """).fetchall()

        collections = {}
        volume_ton = 0.0
        volume_stars = 0.0
        volume_ton_24h = 0.0
        volume_stars_24h = 0.0
        completed_trades_24h = 0

        def collection_bucket(title_value, asset_type_value):
            asset_type = str(asset_type_value or "gift")
            title = str(title_value or "").strip()
            if asset_type == "username":
                return "Telegram usernames"
            if asset_type == "number":
                return "Anonymous numbers"
            hash_parts = title.rsplit("#", 1)
            if len(hash_parts) == 2 and hash_parts[1].strip().isdigit():
                title = hash_parts[0].strip()
            else:
                dash_parts = title.rsplit("-", 1)
                if len(dash_parts) == 2 and dash_parts[1].strip().isdigit():
                    title = dash_parts[0].strip()
            return title or "Telegram gifts"

        def ensure_collection(name):
            return collections.setdefault(name, {
                "collection": name, "active_listings": 0, "trades_24h": 0,
                "volume_ton_24h": 0.0, "volume_stars_24h": 0.0,
                "floor_ton": None, "floor_stars": None,
            })

        for row in active_rows:
            name = collection_bucket(row["title"], row["asset_type"])
            bucket = ensure_collection(name)
            bucket["active_listings"] += 1
            floor_key = "floor_ton" if row["currency"] == "TON" else "floor_stars"
            price = float(row["price"] or 0)
            current_floor = bucket[floor_key]
            bucket[floor_key] = price if current_floor is None else min(current_floor, price)

        for row in completed_rows:
            name = collection_bucket(row["title"], row["asset_type"])
            bucket = ensure_collection(name)
            price = float(row["agreed_amount"] or 0)
            currency = str(row["currency"])
            if currency == "TON":
                volume_ton += price
            elif currency == "XTR":
                volume_stars += price
            if int(row["created_at"] or 0) >= since_24h:
                completed_trades_24h += 1
                bucket["trades_24h"] += 1
                if currency == "TON":
                    volume_ton_24h += price
                    bucket["volume_ton_24h"] += price
                elif currency == "XTR":
                    volume_stars_24h += price
                    bucket["volume_stars_24h"] += price

        collection_list = sorted(
            collections.values(),
            key=lambda item: (item["trades_24h"], item["active_listings"], item["collection"].lower()),
            reverse=True,
        )[:30]
        return {
            "active_listings": counts["active_listings"] or 0,
            "active_buy_requests": counts["active_buy_requests"] or 0,
            "matched_orders": counts["matched_orders"] or 0,
            "completed_trades": len(completed_rows),
            "volume_ton": round(volume_ton, 6),
            "volume_stars": round(volume_stars, 2),
            "floor_ton": counts["floor_ton"],
            "floor_stars": counts["floor_stars"],
            "total_orders": counts["total_orders"] or 0,
            "volume_ton_24h": round(volume_ton_24h, 6),
            "volume_stars_24h": round(volume_stars_24h, 2),
            "completed_trades_24h": completed_trades_24h,
            "collections": collection_list,
        }

    @router.post("/orders", status_code=201)
    async def create_order(
        payload: CreateMarketplaceOrder,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        if payload.kind == "listing" and not _verified_asset_actions_enabled():
            raise HTTPException(
                status_code=409,
                detail="Listing is unavailable until TON Connect ownership verification and real asset import are enabled",
            )
        _enforce_user_rate_limit(str(user["id"]), "create_order", 8)
        if payload.currency == "XTR" and payload.price != payload.price.to_integral_value():
            raise HTTPException(status_code=422, detail="Star listing prices must be whole numbers")
        now = int(time.time())
        order_id = "gr-" + uuid.uuid4().hex[:16]
        owner_id = str(user["id"])
        owner = (user.get("first_name") or user.get("username") or "Telegram user")[:64]

        # Re-check the live chain owner before a TON NFT can become a listing.
        # The initial import is a snapshot; it must not authorize stale ownership.
        if payload.kind == "listing" and payload.asset_id:
            with _db() as conn:
                preflight_asset = conn.execute(
                    "SELECT * FROM verified_assets WHERE id = ? AND status = 'verified'",
                    (payload.asset_id,),
                ).fetchone()
            if preflight_asset is None:
                raise HTTPException(status_code=404, detail="Verified asset not found")
            if preflight_asset["owner_telegram_id"] != owner_id:
                raise HTTPException(status_code=403, detail="Only the verified asset owner can list it")
            if preflight_asset["source"] == "telegram_gift":
                business_gift_ref = get_telegram_business_gift_ref(payload.asset_id, owner_id)
                if business_gift_ref is None:
                    raise HTTPException(
                        status_code=409,
                        detail="A verified Telegram Business gift with transfer permission is required",
                    )
                # Only gifts linked to an active Business connection with transfer
                # rights can be listed. Delivery is gated by transferGift per deal.
            if preflight_asset["source"] == "ton_nft" and not _test_ton_owner_recheck_skipped():
                from ton_connect import list_verified_wallets
                from ton_nft_inventory import fetch_nft_owner, fragment_collection_address
                from ton_nft_chain import verify_nft_collection_membership, verify_nft_owner_onchain
                linked_wallets = [
                    wallet for wallet in list_verified_wallets(owner_id)
                    if wallet.get("network") == "-239"
                ]
                if not linked_wallets:
                    raise HTTPException(status_code=403, detail="A verified TON mainnet wallet is required")

                linked_addresses = {wallet["address"] for wallet in linked_wallets}
                try:
                    if preflight_asset["asset_type"] in {"username", "number"}:
                        collection_address = fragment_collection_address(preflight_asset["asset_type"])
                        if not collection_address:
                            raise HTTPException(status_code=409, detail="Fragment collection is not configured")
                        verified = False
                        for wallet in linked_wallets:
                            proof = await verify_nft_collection_membership(
                                preflight_asset["external_asset_id"],
                                collection_address,
                                wallet["address"],
                            )
                            if proof.get("verified") is True:
                                verified = True
                                break
                    else:
                        proof = await verify_nft_owner_onchain(
                            preflight_asset["external_asset_id"],
                            linked_addresses,
                        )
                        verified = proof.get("verified") is True
                except HTTPException:
                    raise
                except Exception as exc:
                    raise HTTPException(
                        status_code=503,
                        detail="Could not verify current TON contract ownership; listing was not created",
                        headers={"Retry-After": "30"},
                    ) from exc

                if verified:
                    try:
                        trust_info = await fetch_nft_owner(preflight_asset["external_asset_id"])
                    except Exception as exc:
                        raise HTTPException(
                            status_code=503,
                            detail="Could not refresh TON NFT safety metadata; listing was not created",
                            headers={"Retry-After": "30"},
                        ) from exc
                    verified = (
                        trust_info.get("is_scam") is not True
                        and trust_info.get("trust") != "blacklist"
                    )

                if not verified:
                    with _db() as conn:
                        conn.execute(
                            "UPDATE verified_assets SET status = 'revoked', revoked_at = ? WHERE id = ? AND status = 'verified'",
                            (int(time.time()), preflight_asset["id"]),
                        )
                    raise HTTPException(
                        status_code=409,
                        detail="Current TON ownership no longer matches this Telegram account; asset was removed from inventory",
                    )

        with _db() as conn:
            begin_write(conn, f"marketplace-listing:{owner_id}:{payload.asset_id or 'unverified'}")
            asset = None
            if payload.kind == "listing":
                if payload.asset_id:
                    asset = conn.execute(
                        select_for_update(conn, "SELECT * FROM verified_assets WHERE id = ? AND status = 'verified'"),
                        (payload.asset_id,),
                    ).fetchone()
                    if asset is None:
                        conn.rollback()
                        raise HTTPException(status_code=404, detail="Verified asset not found")
                    if asset["owner_telegram_id"] != owner_id:
                        conn.rollback()
                        raise HTTPException(status_code=403, detail="Only the verified asset owner can list it")
                    title = asset["title"]
                    asset_type = asset["asset_type"]
                    asset_id = asset["id"]
                elif _test_unverified_assets_allowed():
                    # Legacy tests only. Production must always supply a server-verified asset ID.
                    title = payload.title
                    asset_type = None
                    asset_id = None
                else:
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="A verified asset must be imported before it can be listed")
            else:
                if payload.asset_id:
                    conn.rollback()
                    raise HTTPException(status_code=422, detail="Buy requests cannot be linked to an owned asset")
                title = payload.title
                asset_type = None
                asset_id = None
            if asset is not None:
                already_listed = conn.execute(
                    "SELECT 1 FROM marketplace_orders WHERE asset_id = ? AND status = 'active' LIMIT 1",
                    (asset_id,),
                ).fetchone()
                if already_listed:
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="This asset already has an active listing")
            conn.execute("""
                INSERT INTO marketplace_orders
                (id, user_id, owner_name, kind, title, asset_id, asset_type, price, currency, status, created_at, updated_at, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """, (order_id, owner_id, owner, payload.kind, title, asset_id, asset_type,
                  format(payload.price, "f"), payload.currency, now, now, payload.note.strip()))
            conn.execute(
                "INSERT INTO marketplace_order_events(order_id, actor_user_id, event_type, created_at) VALUES (?, ?, 'created', ?)",
                (order_id, str(user["id"]), now),
            )
            row = conn.execute(
                """SELECT o.*, a.image_url AS asset_image_url FROM marketplace_orders o
                   LEFT JOIN verified_assets a ON a.id = o.asset_id
                   WHERE o.id = ?""", (order_id,),
            ).fetchone()
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
            begin_write(conn, f"order:{order_id}")
            row = conn.execute(select_for_update(conn, "SELECT * FROM marketplace_orders WHERE id = ?"), (order_id,)).fetchone()
            if row is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Order not found")
            if row["user_id"] != actor_id:
                conn.rollback()
                raise HTTPException(status_code=403, detail="Only the author can edit this order")
            if row["status"] != "active":
                conn.rollback()
                raise HTTPException(status_code=409, detail="Only active orders can be edited")
            if row["currency"] == "XTR" and "price" in changes and changes["price"] != changes["price"].to_integral_value():
                conn.rollback()
                raise HTTPException(status_code=422, detail="Star listing prices must be whole numbers")
            if "title" in changes and row["kind"] == "listing":
                conn.rollback()
                raise HTTPException(status_code=422, detail="Asset metadata is immutable and comes from the verified Telegram gift")
            conn.execute(
                f"UPDATE marketplace_orders SET {', '.join(assignments)} WHERE id = ? AND status = 'active'",
                values,
            )
            updated = conn.execute("SELECT * FROM marketplace_orders WHERE id = ?", (order_id,)).fetchone()
            conn.execute(
                "INSERT INTO marketplace_order_revisions(order_id, actor_user_id, changed_fields, created_at) VALUES (?, ?, ?, ?)",
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
            begin_write(conn, f"order:{order_id}")
            row = conn.execute(select_for_update(conn, "SELECT * FROM marketplace_orders WHERE id = ?"), (order_id,)).fetchone()
            if row is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Order not found")
            if row["user_id"] != actor_id:
                conn.rollback()
                raise HTTPException(status_code=403, detail="Only the author can cancel this order")
            updated = conn.execute(
                "UPDATE marketplace_orders SET status='cancelled', updated_at=? WHERE id=? AND status='active'",
                (now, order_id),
            )
            if updated.rowcount != 1:
                conn.rollback()
                raise HTTPException(status_code=409, detail="This order is no longer active")
            conn.execute(
                "INSERT INTO marketplace_order_events(order_id, actor_user_id, event_type, created_at) VALUES (?, ?, 'cancelled', ?)",
                (order_id, actor_id, now),
            )
            row = conn.execute("SELECT * FROM marketplace_orders WHERE id = ?", (order_id,)).fetchone()
            conn.commit()
        return {"item": _order_dict(row, actor_id)}

    @router.get("/history")
    async def history(x_telegram_init_data: str | None = Header(default=None)):
        user = _user(x_telegram_init_data, verify_telegram_init_data)
        user_id = str(user["id"])
        _enforce_user_rate_limit(user_id, "list_order_history", 30)
        with _db() as conn:
            rows = conn.execute(
                "SELECT * FROM marketplace_orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 200",
                (user_id,),
            ).fetchall()
            result = []
            for row in rows:
                events = conn.execute(
                    "SELECT event_type, created_at FROM marketplace_order_events WHERE order_id = ? ORDER BY created_at ASC, id ASC",
                    (row["id"],),
                ).fetchall()
                revisions = conn.execute(
                    "SELECT created_at FROM marketplace_order_revisions WHERE order_id = ? ORDER BY created_at ASC, id ASC",
                    (row["id"],),
                ).fetchall()
                item = _order_dict(row, user_id)
                timeline = [{"type": event["event_type"], "createdAt": event["created_at"]} for event in events]
                timeline.extend({"type": "updated", "createdAt": revision["created_at"]} for revision in revisions)
                item["events"] = sorted(timeline, key=lambda event: event["createdAt"])
                result.append(item)
        return {"items": result}

    return router
