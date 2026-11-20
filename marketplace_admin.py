"""Role-based administration, user controls and manual reconciliation for HASSLE NFT."""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Callable, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from marketplace import _db, insert_security_audit_event, _enforce_user_rate_limit
from database import begin_write, select_for_update


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "moderator": {
        "dashboard.read", "users.read", "assets.moderate", "reports.read",
        "reports.resolve", "disputes.read", "users.suspend",
    },
    "finance": {
        "dashboard.read", "disputes.read", "disputes.resolve",
        "payments.reconcile", "payments.reconcile.read", "payments.refund", "audit.read",
    },
    "support": {
        "dashboard.read", "users.read", "reports.read", "disputes.read",
        "audit.read", "support.tickets.read", "support.tickets.manage",
    },
    "auditor": {
        "dashboard.read", "users.read", "reports.read", "disputes.read",
        "payments.reconcile.read", "audit.read",
    },
}
ALL_PERMISSIONS = set().union(*ROLE_PERMISSIONS.values()) | {
    "roles.manage", "users.suspend", "assets.moderate", "reports.resolve",
    "disputes.resolve", "payments.refund", "payments.reconcile.read",
}


def _environment_admins() -> set[str]:
    raw = os.getenv("ADMIN_TELEGRAM_IDS", os.getenv("ADMIN_TELEGRAM_ID", ""))
    return {part.strip() for part in raw.split(",") if part.strip().isdigit()}


def get_admin_access(telegram_id: str | int) -> dict[str, Any] | None:
    actor = str(telegram_id).strip()
    if not actor.isdigit():
        return None
    if actor in _environment_admins():
        return {"telegram_id": actor, "role": "super_admin", "permissions": sorted(ALL_PERMISSIONS)}
    with _db() as conn:
        row = conn.execute(
            """SELECT role, permissions_json, revoked_at
               FROM marketplace_admin_roles WHERE telegram_id = ?""",
            (actor,),
        ).fetchone()
    if row is None or row["revoked_at"] is not None:
        return None
    try:
        permissions = json.loads(row["permissions_json"] or "[]")
    except (TypeError, ValueError):
        permissions = []
    if not isinstance(permissions, list):
        permissions = []
    allowed = ROLE_PERMISSIONS.get(str(row["role"]), set())
    permissions = sorted({p for p in permissions if isinstance(p, str) and p in allowed})
    return {"telegram_id": actor, "role": str(row["role"]), "permissions": permissions}


def get_admin_ids_with_permission(permission: str) -> set[str]:
    """Return active, configured administrators eligible for a specific two-person approval."""
    ids = {admin_id for admin_id in _environment_admins() if not user_is_suspended(admin_id)}
    if permission not in ALL_PERMISSIONS:
        return ids
    with _db() as conn:
        rows = conn.execute(
            "SELECT telegram_id FROM marketplace_admin_roles WHERE revoked_at IS NULL"
        ).fetchall()
    for row in rows:
        telegram_id = str(row["telegram_id"])
        if user_is_suspended(telegram_id):
            continue
        access = get_admin_access(telegram_id)
        if access is not None and permission in access["permissions"]:
            ids.add(telegram_id)
    return ids


def user_is_suspended(telegram_id: str | int) -> bool:
    actor = str(telegram_id).strip()
    if not actor.isdigit():
        return False
    with _db() as conn:
        row = conn.execute(
            "SELECT status FROM marketplace_user_controls WHERE telegram_id = ?",
            (actor,),
        ).fetchone()
    return bool(row and row["status"] == "suspended")


class AssetModerationRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class UserControlRequest(BaseModel):
    status: Literal["active", "suspended"]
    reason: str = Field(min_length=1, max_length=500)


class AdminRoleRequest(BaseModel):
    role: Literal["moderator", "finance", "support", "auditor"]
    active: bool = True


class ReconciliationResolutionRequest(BaseModel):
    action: Literal["resolve", "ignore"]
    note: str = Field(min_length=1, max_length=1000)


class SupportTicketCreateRequest(BaseModel):
    ticket_type: Literal["payment", "account", "asset", "other"]
    subject: str = Field(min_length=3, max_length=120)
    message: str = Field(min_length=10, max_length=4000)


class SupportTicketUpdateRequest(BaseModel):
    status: Literal["in_progress", "resolved", "closed"]
    note: str = Field(min_length=1, max_length=1000)


def create_marketplace_admin_router(verify_credential: Callable[[str], dict]) -> APIRouter:
    root_router = APIRouter()
    router = APIRouter(prefix="/api/marketplace/admin", tags=["marketplace-admin"])
    support_router = APIRouter(prefix="/api/marketplace/support", tags=["marketplace-support"])

    def require_permission(credential: str | None, permission: str) -> dict:
        if not credential:
            raise HTTPException(status_code=401, detail="Administrator authentication required")
        user = verify_credential(credential)
        access = get_admin_access(user["id"])
        if access is None or permission not in access["permissions"]:
            raise HTTPException(status_code=403, detail="Administrator permission required")
        return {**user, "admin_role": access["role"], "admin_permissions": access["permissions"]}

    @router.get("/overview")
    async def admin_overview(x_telegram_init_data: str | None = Header(default=None)):
        require_permission(x_telegram_init_data, "dashboard.read")
        with _db() as conn:
            counts = {
                "users": int(conn.execute("SELECT COUNT(*) FROM telegram_users").fetchone()[0]),
                "suspended_users": int(conn.execute("SELECT COUNT(*) FROM marketplace_user_controls WHERE status='suspended'").fetchone()[0]),
                "verified_assets": int(conn.execute("SELECT COUNT(*) FROM verified_assets WHERE status='verified'").fetchone()[0]),
                "active_listings": int(conn.execute("SELECT COUNT(*) FROM marketplace_orders WHERE kind='listing' AND status='active'").fetchone()[0]),
                "open_reports": int(conn.execute("SELECT COUNT(*) FROM marketplace_order_reports WHERE status='open'").fetchone()[0]),
                "disputed_deals": int(conn.execute("SELECT COUNT(*) FROM marketplace_deals WHERE status='disputed'").fetchone()[0]),
                "open_reconciliation": int(conn.execute("SELECT COUNT(*) FROM marketplace_manual_reconciliation_items WHERE status='open'").fetchone()[0]),
                "open_support_tickets": int(conn.execute("SELECT COUNT(*) FROM marketplace_support_tickets WHERE status IN ('open','in_progress')").fetchone()[0]),
            }
        return {"counts": counts}

    @router.get("/assets")
    async def admin_list_assets(
        q: str = Query(default="", max_length=128),
        status: Literal["verified", "revoked", "all"] = "verified",
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        require_permission(x_telegram_init_data, "assets.moderate")
        needle = f"%{q.strip()}%"
        where = "" if status == "all" else " AND a.status = ?"
        values: list[Any] = [needle, needle, needle]
        if status != "all":
            values.append(status)
        values.extend([limit, offset])
        with _db() as conn:
            rows = conn.execute(
                f"""SELECT a.id, a.source, a.external_asset_id, a.owner_telegram_id,
                           a.title, a.asset_type, a.status, a.verified_at, a.revoked_at,
                           u.username AS owner_username
                    FROM verified_assets a
                    LEFT JOIN telegram_users u ON u.telegram_id=a.owner_telegram_id
                    WHERE (a.id LIKE ? OR a.external_asset_id LIKE ? OR a.title LIKE ?){where}
                    ORDER BY a.verified_at DESC LIMIT ? OFFSET ?""",
                values,
            ).fetchall()
            total = int(conn.execute(
                f"""SELECT COUNT(*) FROM verified_assets a
                    WHERE (a.id LIKE ? OR a.external_asset_id LIKE ? OR a.title LIKE ?){where}""",
                values[:-2],
            ).fetchone()[0])
        return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    @router.post("/assets/{asset_id}/revoke")
    async def admin_revoke_asset(
        asset_id: str,
        payload: AssetModerationRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        admin = require_permission(x_telegram_init_data, "assets.moderate")
        actor = str(admin["id"])
        reason = payload.reason.strip()
        if not reason:
            raise HTTPException(status_code=422, detail="A moderation reason is required")
        now = int(time.time())
        with _db() as conn:
            begin_write(conn, f"asset-moderation:{asset_id}")
            asset = conn.execute(
                select_for_update(conn, "SELECT * FROM verified_assets WHERE id=?"),
                (asset_id,),
            ).fetchone()
            if asset is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Asset not found")
            if asset["status"] != "verified":
                conn.rollback()
                raise HTTPException(status_code=409, detail="Only currently verified assets can be revoked")
            open_deal = conn.execute(
                """SELECT d.id FROM marketplace_deals d
                   JOIN marketplace_orders o ON o.id=d.order_id
                   WHERE o.asset_id=? AND d.status IN ('payment_pending','paid','delivery_pending','disputed')
                   LIMIT 1""",
                (asset_id,),
            ).fetchone()
            if open_deal is not None:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Asset is attached to a paid or disputed deal; resolve the deal first")
            active_listing = conn.execute(
                "SELECT id FROM marketplace_orders WHERE asset_id=? AND status IN ('active','matched') LIMIT 1",
                (asset_id,),
            ).fetchone()
            if active_listing is not None:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Remove the asset listing and resolve any reservation before revocation")
            conn.execute(
                "UPDATE verified_assets SET status='revoked', revoked_at=? WHERE id=? AND status='verified'",
                (now, asset_id),
            )
            insert_security_audit_event(
                conn,
                actor_telegram_id=actor,
                action="admin_asset_revoked",
                target_type="asset",
                target_id=asset_id,
                metadata={"reason_hash": __import__("hashlib").sha256(reason.encode("utf-8")).hexdigest()},
                now=now,
            )
            conn.commit()
        return {"asset_id": asset_id, "status": "revoked", "revoked_at": now}

    @router.get("/users")
    async def admin_list_users(
        q: str = Query(default="", max_length=128),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        require_permission(x_telegram_init_data, "users.read")
        needle = f"%{q.strip()}%"
        with _db() as conn:
            rows = conn.execute(
                """SELECT u.telegram_id, u.username, u.first_name, u.last_name,
                          u.created_at, COALESCE(c.status, 'active') AS account_status,
                          COALESCE(c.reason, '') AS control_reason,
                          COALESCE(r.role, CASE WHEN u.telegram_id IN ('') THEN '' ELSE '' END) AS admin_role,
                          (SELECT COUNT(*) FROM marketplace_orders o WHERE o.user_id=u.telegram_id) AS order_count,
                          (SELECT COUNT(*) FROM verified_assets a WHERE a.owner_telegram_id=u.telegram_id AND a.status='verified') AS verified_asset_count
                   FROM telegram_users u
                   LEFT JOIN marketplace_user_controls c ON c.telegram_id=u.telegram_id
                   LEFT JOIN marketplace_admin_roles r ON r.telegram_id=u.telegram_id AND r.revoked_at IS NULL
                   WHERE (? = '' OR u.telegram_id LIKE ? OR u.username LIKE ? OR u.first_name LIKE ? OR u.last_name LIKE ?)
                   ORDER BY u.created_at DESC LIMIT ? OFFSET ?""",
                (q.strip(), needle, needle, needle, needle, limit, offset),
            ).fetchall()
            total = int(conn.execute(
                """SELECT COUNT(*) FROM telegram_users u
                   WHERE (? = '' OR u.telegram_id LIKE ? OR u.username LIKE ? OR u.first_name LIKE ? OR u.last_name LIKE ?)""",
                (q.strip(), needle, needle, needle, needle),
            ).fetchone()[0])
        items = []
        for row in rows:
            item = dict(row)
            item["is_environment_admin"] = item["telegram_id"] in _environment_admins()
            if item["is_environment_admin"]:
                item["admin_role"] = "super_admin"
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @router.patch("/users/{telegram_id}")
    async def admin_update_user(
        telegram_id: str,
        payload: UserControlRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        admin = require_permission(x_telegram_init_data, "users.suspend")
        actor = str(admin["id"])
        target = str(telegram_id).strip()
        if not target.isdigit():
            raise HTTPException(status_code=422, detail="Telegram user ID must be numeric")
        if target == actor or get_admin_access(target) is not None:
            raise HTTPException(status_code=409, detail="Administrator accounts cannot be suspended through user moderation")
        reason = payload.reason.strip()
        if not reason:
            raise HTTPException(status_code=422, detail="A moderation reason is required")
        now = int(time.time())
        with _db() as conn:
            begin_write(conn, f"user-control:{target}")
            exists = conn.execute("SELECT 1 FROM telegram_users WHERE telegram_id=?", (target,)).fetchone()
            if exists is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="User not found")
            conn.execute(
                """INSERT INTO marketplace_user_controls
                   (telegram_id, status, reason, changed_by, changed_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(telegram_id) DO UPDATE SET
                     status=excluded.status, reason=excluded.reason,
                     changed_by=excluded.changed_by, changed_at=excluded.changed_at""",
                (target, payload.status, reason, actor, now),
            )
            if payload.status == "suspended":
                conn.execute(
                    "UPDATE telegram_sessions SET revoked_at=? WHERE telegram_id=? AND revoked_at IS NULL",
                    (now, target),
                )
            insert_security_audit_event(
                conn,
                actor_telegram_id=actor,
                action="admin_user_suspended" if payload.status == "suspended" else "admin_user_reactivated",
                target_type="user",
                target_id=target,
                metadata={"reason": reason[:120]},
                now=now,
            )
            conn.commit()
        return {"telegram_id": target, "status": payload.status, "changed_at": now}

    @router.get("/roles")
    async def admin_list_roles(x_telegram_init_data: str | None = Header(default=None)):
        require_permission(x_telegram_init_data, "roles.manage")
        with _db() as conn:
            rows = conn.execute(
                """SELECT telegram_id, role, permissions_json, assigned_by, created_at, updated_at, revoked_at
                   FROM marketplace_admin_roles ORDER BY created_at DESC"""
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["permissions"] = json.loads(item.pop("permissions_json") or "[]")
            except (TypeError, ValueError):
                item["permissions"] = []
            items.append(item)
        return {
            "items": items,
            "environment_super_admins": sorted(_environment_admins()),
            "available_roles": {role: sorted(perms) for role, perms in ROLE_PERMISSIONS.items()},
        }

    @router.put("/roles/{telegram_id}")
    async def admin_set_role(
        telegram_id: str,
        payload: AdminRoleRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        admin = require_permission(x_telegram_init_data, "roles.manage")
        actor = str(admin["id"])
        target = str(telegram_id).strip()
        if not target.isdigit() or target == actor or target in _environment_admins():
            raise HTTPException(status_code=409, detail="A different non-root Telegram user ID is required")
        now = int(time.time())
        with _db() as conn:
            begin_write(conn, f"admin-role:{target}")
            exists = conn.execute("SELECT 1 FROM telegram_users WHERE telegram_id=?", (target,)).fetchone()
            if exists is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="User not found")
            if payload.active:
                conn.execute(
                    """INSERT INTO marketplace_admin_roles
                       (telegram_id, role, permissions_json, assigned_by, created_at, updated_at, revoked_at)
                       VALUES (?, ?, ?, ?, ?, ?, NULL)
                       ON CONFLICT(telegram_id) DO UPDATE SET
                         role=excluded.role, permissions_json=excluded.permissions_json,
                         assigned_by=excluded.assigned_by, updated_at=excluded.updated_at, revoked_at=NULL""",
                    (target, payload.role, json.dumps(sorted(ROLE_PERMISSIONS[payload.role])), actor, now, now),
                )
                action = "admin_role_assigned"
            else:
                conn.execute(
                    """UPDATE marketplace_admin_roles SET revoked_at=?, updated_at=?, assigned_by=?
                       WHERE telegram_id=? AND revoked_at IS NULL""",
                    (now, now, actor, target),
                )
                action = "admin_role_revoked"
            insert_security_audit_event(
                conn,
                actor_telegram_id=actor,
                action=action,
                target_type="admin_role",
                target_id=target,
                metadata={"reason": payload.role},
                now=now,
            )
            conn.commit()
        return {"telegram_id": target, "role": payload.role, "active": payload.active, "updated_at": now}

    @router.get("/disputes")
    async def admin_list_disputes(
        limit: int = Query(default=50, ge=1, le=100),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        require_permission(x_telegram_init_data, "disputes.read")
        with _db() as conn:
            rows = conn.execute(
                """SELECT d.id, d.listing_owner_id, d.offer_sender_id, d.agreed_amount,
                          d.currency, d.status, d.created_at, o.title,
                          (SELECT COUNT(*) FROM marketplace_dispute_resolution_approvals a WHERE a.deal_id=d.id) AS approvals,
                          (SELECT COUNT(*) FROM marketplace_dispute_resolution_approvals a WHERE a.deal_id=d.id AND a.decision='refund_buyer') AS refund_approvals,
                          (SELECT COUNT(*) FROM marketplace_dispute_resolution_approvals a WHERE a.deal_id=d.id AND a.decision='release_to_seller') AS seller_release_approvals
                   FROM marketplace_deals d JOIN marketplace_orders o ON o.id=d.order_id
                   WHERE d.status='disputed' ORDER BY d.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": len(rows)}

    @router.get("/reconciliation")
    async def admin_list_reconciliation(
        status: Literal["open", "resolved", "ignored"] = "open",
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        require_permission(x_telegram_init_data, "payments.reconcile.read")
        with _db() as conn:
            rows = conn.execute(
                """SELECT id, kind, deal_id, payment_intent_id, provider_reference,
                          summary, status, resolution_note, created_at, resolved_by, resolved_at
                   FROM marketplace_manual_reconciliation_items
                   WHERE status=? ORDER BY created_at ASC LIMIT ? OFFSET ?""",
                (status, limit, offset),
            ).fetchall()
            total = int(conn.execute(
                "SELECT COUNT(*) FROM marketplace_manual_reconciliation_items WHERE status=?",
                (status,),
            ).fetchone()[0])
        return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    @router.post("/reconciliation/{item_id}")
    async def admin_resolve_reconciliation(
        item_id: str,
        payload: ReconciliationResolutionRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        admin = require_permission(x_telegram_init_data, "payments.reconcile")
        actor = str(admin["id"])
        now = int(time.time())
        with _db() as conn:
            begin_write(conn, f"reconciliation:{item_id}")
            item = conn.execute(
                select_for_update(conn, "SELECT * FROM marketplace_manual_reconciliation_items WHERE id=?"),
                (item_id,),
            ).fetchone()
            if item is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Reconciliation item not found")
            if item["status"] != "open":
                conn.rollback()
                raise HTTPException(status_code=409, detail="Reconciliation item is already resolved")
            status = "resolved" if payload.action == "resolve" else "ignored"
            conn.execute(
                """UPDATE marketplace_manual_reconciliation_items
                   SET status=?, resolution_note=?, resolved_by=?, resolved_at=?
                   WHERE id=? AND status='open'""",
                (status, payload.note.strip(), actor, now, item_id),
            )
            insert_security_audit_event(
                conn,
                actor_telegram_id=actor,
                action="admin_reconciliation_resolved",
                target_type="reconciliation",
                target_id=item_id,
                metadata={"reason": payload.action},
                now=now,
            )
            conn.commit()
        return {"id": item_id, "status": status, "resolved_by": actor, "resolved_at": now}

    @router.get("/support-tickets")
    async def admin_list_support_tickets(
        status: Literal["open", "in_progress", "resolved", "closed", "all"] = "open",
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        admin = require_permission(x_telegram_init_data, "support.tickets.read")
        _enforce_user_rate_limit(str(admin["id"]), "list_support_tickets", 30)
        where = "" if status == "all" else " WHERE status=?"
        values: list[Any] = [] if status == "all" else [status]
        with _db() as conn:
            rows = conn.execute(
                f"""SELECT id, telegram_id, username, ticket_type, subject, message,
                           status, assigned_to, resolution_note, created_at, updated_at, resolved_at
                    FROM marketplace_support_tickets{where}
                    ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                             created_at ASC LIMIT ? OFFSET ?""",
                (*values, limit, offset),
            ).fetchall()
            total = int(conn.execute(
                f"SELECT COUNT(*) FROM marketplace_support_tickets{where}", values
            ).fetchone()[0])
        return {"items":[dict(row) for row in rows], "total":total, "limit":limit, "offset":offset}

    @router.patch("/support-tickets/{ticket_id}")
    async def admin_update_support_ticket(
        ticket_id: str,
        payload: SupportTicketUpdateRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        admin = require_permission(x_telegram_init_data, "support.tickets.manage")
        actor = str(admin["id"])
        _enforce_user_rate_limit(actor, "update_support_ticket", 30)
        now = int(time.time())
        with _db() as conn:
            begin_write(conn, f"support-ticket:{ticket_id}")
            ticket = conn.execute(
                select_for_update(conn, "SELECT * FROM marketplace_support_tickets WHERE id=?"),
                (ticket_id,),
            ).fetchone()
            if ticket is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Support ticket not found")
            if ticket["status"] in {"resolved", "closed"}:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Support ticket is already closed")
            assigned_to = actor if payload.status == "in_progress" else (ticket["assigned_to"] or actor)
            resolved_at = now if payload.status in {"resolved", "closed"} else None
            conn.execute(
                """UPDATE marketplace_support_tickets
                   SET status=?, assigned_to=?, resolution_note=?, updated_at=?, resolved_at=?
                   WHERE id=? AND status IN ('open','in_progress')""",
                (payload.status, assigned_to, payload.note.strip(), now, resolved_at, ticket_id),
            )
            action = {
                "in_progress": "admin_support_ticket_assigned",
                "resolved": "admin_support_ticket_resolved",
                "closed": "admin_support_ticket_closed",
            }[payload.status]
            insert_security_audit_event(
                conn, actor_telegram_id=actor, action=action,
                target_type="support_ticket", target_id=ticket_id,
                metadata={"status":payload.status,"reason":payload.note.strip()[:200]},
                now=now,
            )
            conn.commit()
        return {"id":ticket_id,"status":payload.status,"assigned_to":assigned_to,"resolved_at":resolved_at}

    @support_router.post("/tickets", status_code=201)
    async def create_support_ticket(
        payload: SupportTicketCreateRequest,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        if not x_telegram_init_data:
            raise HTTPException(status_code=401, detail="Telegram authentication required")
        user = verify_credential(x_telegram_init_data)
        telegram_id = str(user["id"])
        _enforce_user_rate_limit(telegram_id, "create_support_ticket", 5)
        now = int(time.time())
        ticket_id = "spt_" + uuid.uuid4().hex
        with _db() as conn:
            conn.execute(
                """INSERT INTO marketplace_support_tickets
                   (id, telegram_id, username, ticket_type, subject, message, status,
                    assigned_to, resolution_note, created_at, updated_at, resolved_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'open', NULL, '', ?, ?, NULL)""",
                (ticket_id, telegram_id, str(user.get("username") or "")[:64],
                 payload.ticket_type, payload.subject.strip(), payload.message.strip(), now, now),
            )
            conn.commit()
        return {"id":ticket_id,"status":"open","created_at":now}

    @support_router.get("/tickets/me")
    async def list_my_support_tickets(
        limit: int = Query(default=20, ge=1, le=50),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        if not x_telegram_init_data:
            raise HTTPException(status_code=401, detail="Telegram authentication required")
        user = verify_credential(x_telegram_init_data)
        _enforce_user_rate_limit(str(user["id"]), "list_my_support_tickets", 30)
        with _db() as conn:
            rows = conn.execute(
                """SELECT id, ticket_type, subject, message, status, resolution_note,
                          created_at, updated_at, resolved_at
                   FROM marketplace_support_tickets
                   WHERE telegram_id=? ORDER BY created_at DESC LIMIT ?""",
                (str(user["id"]), limit),
            ).fetchall()
        return {"items":[dict(row) for row in rows]}

    @router.get("/audit")
    async def admin_audit(
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=100000),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        require_permission(x_telegram_init_data, "audit.read")
        with _db() as conn:
            rows = conn.execute(
                """SELECT id, actor_telegram_id, action, target_type, target_hash,
                          outcome, metadata_json, created_at
                   FROM security_audit_events ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
            total = int(conn.execute("SELECT COUNT(*) FROM security_audit_events").fetchone()[0])
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            except (TypeError, ValueError):
                item["metadata"] = {}
            items.append(item)
        return {"items": items, "total": total}

    root_router.include_router(router)
    root_router.include_router(support_router)
    return root_router
