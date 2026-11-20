"""Read-only operations console and narrowly scoped admin account controls."""
from __future__ import annotations

import os
import time
from fastapi import APIRouter, Header, HTTPException, Query

import marketplace
from database import begin_write, select_for_update


def create_marketplace_admin_router(verify_admin):
    router = APIRouter(prefix="/api/marketplace/admin", tags=["marketplace-admin"])

    permissions = {
        "owner": {"dashboard", "users_read", "users_manage", "assets_read", "reports", "reconciliation_read", "deals_read"},
        "moderator": {"dashboard", "assets_read", "reports"},
        "support": {"dashboard", "users_read", "users_manage", "reports", "deals_read"},
        "finance": {"dashboard", "reconciliation_read", "deals_read"},
    }

    def admin_user(credential, permission="dashboard"):
        if verify_admin is None:
            raise HTTPException(status_code=503, detail="Admin access is not configured")
        user = verify_admin(credential)
        role = user.get("admin_role", "owner")
        if role not in permissions or permission not in permissions[role]:
            raise HTTPException(status_code=403, detail="Administrator role does not grant this permission")
        marketplace._enforce_user_rate_limit(str(user["id"]), "admin_console", 60)
        return user

    @router.get("/roles/me")
    async def my_admin_role(x_telegram_init_data: str | None = Header(default=None)):
        admin = admin_user(x_telegram_init_data)
        role = admin.get("admin_role", "owner")
        return {"telegram_id": str(admin["id"]), "role": role, "permissions": sorted(permissions[role])}

    @router.get("/overview")
    async def overview(x_telegram_init_data: str | None = Header(default=None)):
        admin_user(x_telegram_init_data)
        with marketplace._db() as conn:
            def count(sql):
                row = conn.execute(sql).fetchone()
                return int(row[0] or 0)
            return {
                "users": count("SELECT COUNT(*) FROM telegram_users"),
                "active_listings": count("SELECT COUNT(*) FROM marketplace_orders WHERE kind='listing' AND status='active'"),
                "open_reports": count("SELECT COUNT(*) FROM marketplace_order_reports WHERE status='open'"),
                "verified_assets": count("SELECT COUNT(*) FROM verified_assets WHERE status='verified'"),
                "open_disputes": count("SELECT COUNT(*) FROM marketplace_deals WHERE status='disputed'"),
                "pending_payments": count("SELECT COUNT(*) FROM marketplace_payment_intents WHERE status IN ('created','pending')"),
                "generated_at": int(time.time()),
            }

    @router.get("/users")
    async def users(
        q: str = Query(default="", max_length=64),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=10000),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        admin_user(x_telegram_init_data, "users_read")
        search = q.strip()
        where = ""
        values: list[object] = []
        if search:
            if not search.isdigit() and not search.startswith("@"):
                raise HTTPException(status_code=400, detail="Search by Telegram ID or username")
            if search.startswith("@"):
                where = " WHERE LOWER(username) LIKE LOWER(?)"
                values.append("%" + search[1:] + "%")
            else:
                where = " WHERE telegram_id = ?"
                values.append(search)
        with marketplace._db() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM telegram_users" + where, values).fetchone()[0])
            rows = conn.execute(
                """SELECT u.telegram_id, u.username, u.first_name, u.last_name,
                          u.is_premium, u.created_at, u.updated_at,
                          (SELECT COUNT(*) FROM marketplace_orders o WHERE o.user_id=u.telegram_id) AS order_count,
                          (SELECT COUNT(*) FROM verified_assets a WHERE a.owner_telegram_id=u.telegram_id AND a.status='verified') AS verified_assets,
                          (SELECT COUNT(*) FROM telegram_sessions s WHERE s.telegram_id=u.telegram_id AND s.revoked_at IS NULL AND s.expires_at>?) AS active_sessions
                   FROM telegram_users u""" + where +
                " ORDER BY u.updated_at DESC LIMIT ? OFFSET ?",
                (int(time.time()), *values, limit, offset),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    @router.post("/users/{telegram_id}/revoke-sessions")
    async def revoke_user_sessions(
        telegram_id: str,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        admin = admin_user(x_telegram_init_data, "users_manage")
        if not telegram_id.isdigit():
            raise HTTPException(status_code=404, detail="User not found")
        now = int(time.time())
        with marketplace._db() as conn:
            begin_write(conn, f"admin-revoke-sessions:{telegram_id}")
            exists = conn.execute("SELECT 1 FROM telegram_users WHERE telegram_id=?", (telegram_id,)).fetchone()
            if exists is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="User not found")
            result = conn.execute(
                "UPDATE telegram_sessions SET revoked_at=? WHERE telegram_id=? AND revoked_at IS NULL AND expires_at>?",
                (now, telegram_id, now),
            )
            revoked = int(result.rowcount)
            marketplace.insert_security_audit_event(
                conn, actor_telegram_id=str(admin["id"]),
                action="telegram_sessions_revoked_all", target_type="account",
                target_id=telegram_id, metadata={"revoked_count": revoked}, now=now,
            )
            conn.commit()
        return {"telegram_id": telegram_id, "revoked_sessions": revoked}

    @router.get("/assets")
    async def assets(
        status: str = Query(default="verified", pattern="^(verified|revoked|all)$"),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=10000),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        admin_user(x_telegram_init_data, "assets_read")
        where = "" if status == "all" else " WHERE a.status=?"
        values: list[object] = [] if status == "all" else [status]
        with marketplace._db() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM verified_assets a" + where, values).fetchone()[0])
            rows = conn.execute(
                """SELECT a.id, a.source, a.external_asset_id, a.owner_telegram_id,
                          a.title, a.asset_type, a.status, a.verified_at, a.revoked_at,
                          (SELECT COUNT(*) FROM marketplace_orders o WHERE o.asset_id=a.id AND o.status='active') AS active_listings
                   FROM verified_assets a""" + where +
                " ORDER BY a.verified_at DESC LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    @router.get("/reconciliation")
    async def reconciliation(
        limit: int = Query(default=50, ge=1, le=100),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        admin_user(x_telegram_init_data, "reconciliation_read")
        with marketplace._db() as conn:
            payments = conn.execute(
                """SELECT p.id, p.deal_id, p.provider, p.currency, p.amount, p.status,
                          p.created_at, p.updated_at, p.provider_reference
                   FROM marketplace_payment_intents p
                   WHERE p.status IN ('created','pending')
                   ORDER BY p.updated_at ASC LIMIT ?""", (limit,)
            ).fetchall()
            transfers = conn.execute(
                """SELECT e.id, e.deal_id, e.event_type, e.event_data, e.created_at
                   FROM marketplace_escrow_events e
                   WHERE e.event_type IN ('asset_transfer_requested','asset_transfer_submitted')
                     AND NOT EXISTS (
                       SELECT 1 FROM marketplace_escrow_events c
                       WHERE c.deal_id=e.deal_id AND c.event_type IN ('asset_transfer_confirmed','asset_transfer_rejected')
                         AND c.created_at>=e.created_at
                     )
                   ORDER BY e.created_at ASC LIMIT ?""", (limit,)
            ).fetchall()
        return {
            "payments": [dict(row) for row in payments],
            "transfers": [dict(row) for row in transfers],
            "note": "Review provider records before taking action. This queue does not execute refunds, payouts or transfers.",
        }

    @router.get("/deals")
    async def deals(
        status: str = Query(default="disputed", pattern="^(disputed|all|awaiting_payment|paid|completed|cancelled)$"),
        limit: int = Query(default=50, ge=1, le=100),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        admin_user(x_telegram_init_data, "deals_read")
        where = "" if status == "all" else " WHERE d.status=?"
        values: list[object] = [] if status == "all" else [status]
        with marketplace._db() as conn:
            rows = conn.execute(
                """SELECT d.id, d.status, d.offer_sender_id AS buyer_telegram_id, d.listing_owner_id AS seller_telegram_id,
                          d.created_at
                   FROM marketplace_deals d""" + where +
                " ORDER BY d.created_at DESC LIMIT ?", (*values, limit)
            ).fetchall()
        return {"items": [dict(row) for row in rows]}

    return router
