"""Read-only operations console and narrowly scoped admin account controls."""
from __future__ import annotations

import os
import time
from fastapi import APIRouter, Body, Header, HTTPException, Query

import marketplace
from database import begin_write, select_for_update


def create_marketplace_admin_router(verify_admin):
    router = APIRouter(prefix="/api/marketplace/admin", tags=["marketplace-admin"])

    permissions = {
        "owner": {"dashboard", "users_read", "users_manage", "assets_read", "assets_moderate", "reports", "reconciliation_read", "deals_read", "treasury_read"},
        "moderator": {"dashboard", "assets_read", "assets_moderate", "reports"},
        "support": {"dashboard", "users_read", "users_manage", "reports", "deals_read"},
        "finance": {"dashboard", "reconciliation_read", "deals_read", "treasury_read"},
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

    @router.get("/treasury")
    async def treasury_status(x_telegram_init_data: str | None = Header(default=None)):
        """Read-only on-chain status for the pinned HASSLE 2-of-3 treasury."""
        admin_user(x_telegram_init_data, "treasury_read")
        expected_address = os.getenv("HASSLE_TON_MULTISIG_EXPECTED_ADDRESS", "").strip()
        code_hash = os.getenv("HASSLE_TON_MULTISIG_CODE_HASH", "").strip().lower()
        data_hash = os.getenv("HASSLE_TON_MULTISIG_DATA_HASH", "").strip().lower()
        if not (expected_address and code_hash and data_hash):
            return {
                "status": "configuration_missing",
                "network": "mainnet",
                "address": expected_address or None,
                "threshold": 2,
                "signer_count": 3,
                "proposer_count": 0,
                "next_order_seqno": None,
                "verified": False,
            }
        try:
            from ton_multisig import fetch_ton_multisig_config, is_two_of_three_multisig
            config = await fetch_ton_multisig_config(expected_address)
            verified = is_two_of_three_multisig(config, expected_address)
            return {
                "status": "verified" if verified else "configuration_mismatch",
                "network": "mainnet",
                "address": expected_address,
                "threshold": config.get("threshold"),
                "signer_count": len(config.get("signers", [])),
                "proposer_count": len(config.get("proposers", [])),
                "next_order_seqno": config.get("next_order_seqno"),
                "verified": verified,
            }
        except Exception:
            # Never turn an RPC outage, uninitialized address, or malformed getter
            # response into a false "deployed" or "verified" treasury state.
            return {
                "status": "unavailable",
                "network": "mainnet",
                "address": expected_address,
                "threshold": 2,
                "signer_count": 3,
                "proposer_count": 0,
                "next_order_seqno": None,
                "verified": False,
            }

    @router.post("/treasury/orders/prepare")
    async def prepare_treasury_order(
        request: dict = Body(...),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """Prepare an unsigned Multisig V2 new_order transaction for TON Connect."""
        admin_user(x_telegram_init_data, "treasury_read")
        expected_address = os.getenv("HASSLE_TON_MULTISIG_EXPECTED_ADDRESS", "").strip()
        if not expected_address:
            raise HTTPException(status_code=409, detail="Treasury is not configured")
        wallet_address = request.get("wallet_address")
        action_boc = request.get("actions_boc")
        expiration_date = request.get("expiration_date")
        if not isinstance(wallet_address, str) or not wallet_address.strip():
            raise HTTPException(status_code=422, detail="A connected signer wallet address is required")
        if not isinstance(action_boc, str) or not action_boc:
            raise HTTPException(status_code=422, detail="Multisig action cell BOC is required")
        if isinstance(expiration_date, bool) or not isinstance(expiration_date, int):
            raise HTTPException(status_code=422, detail="A valid order expiration is required")
        try:
            from ton_multisig import (
                _raw_address,
                fetch_ton_multisig_config,
                is_two_of_three_multisig,
            )
            from ton_multisig_transactions import build_new_order_payload

            config = await fetch_ton_multisig_config(expected_address)
            if not is_two_of_three_multisig(config, expected_address):
                raise HTTPException(status_code=409, detail="On-chain treasury verification failed")
            signers = config.get("signers")
            if not isinstance(signers, list) or len(signers) != 3:
                raise HTTPException(status_code=409, detail="Treasury signer set is invalid")
            connected_raw = _raw_address(wallet_address)
            signer_index = next(
                (index for index, signer in enumerate(signers)
                 if _raw_address(str(signer)) == connected_raw),
                None,
            )
            if signer_index is None:
                raise HTTPException(status_code=403, detail="Connected wallet is not a configured treasury signer")
            prepared = build_new_order_payload(
                action_boc,
                signer_index=signer_index,
                signer_addresses=signers,
                expiration_date=expiration_date,
            )
            return {
                "status": "prepared_unsigned",
                "network": "-239",
                "treasury_address": expected_address,
                "transaction": {
                    "validUntil": expiration_date,
                    "network": "-239",
                    "messages": [{
                        "address": expected_address,
                        "amount": prepared["attached_value_nano"],
                        "payload": prepared["payload"],
                    }],
                },
                "review": {
                    "payload_sha256": prepared["payload_sha256"],
                    "action_count": prepared["action_count"],
                    "actions": prepared["actions"],
                    "approval_safe": False,
                    "requires_independent_signer_review": True,
                },
            }
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Treasury could not be verified") from exc

    @router.get("/treasury/orders")
    async def treasury_orders(
        limit: int = Query(default=20, ge=1, le=50),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        """List recent child orders only after exact on-chain treasury verification."""
        admin_user(x_telegram_init_data, "treasury_read")
        expected_address = os.getenv("HASSLE_TON_MULTISIG_EXPECTED_ADDRESS", "").strip()
        if not expected_address:
            return {"status": "configuration_missing", "items": []}
        try:
            from ton_multisig import (
                fetch_ton_multisig_config,
                fetch_ton_multisig_orders,
                is_two_of_three_multisig,
            )
            config = await fetch_ton_multisig_config(expected_address)
            if not is_two_of_three_multisig(config, expected_address):
                return {"status": "treasury_not_verified", "items": []}
            items = await fetch_ton_multisig_orders(
                expected_address,
                int(config["next_order_seqno"]),
                limit=limit,
            )
            return {"status": "verified", "items": items}
        except Exception:
            # RPC errors and malformed data fail closed; never expose guessed orders.
            return {"status": "unavailable", "items": []}

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

    @router.post("/assets/{asset_id}/revoke")
    async def revoke_asset(
        asset_id: str,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        admin = admin_user(x_telegram_init_data, "assets_moderate")
        now = int(time.time())
        with marketplace._db() as conn:
            begin_write(conn, f"admin-revoke-asset:{asset_id}")
            asset = conn.execute(
                select_for_update(conn, "SELECT id, owner_telegram_id, status FROM verified_assets WHERE id=?"),
                (asset_id,),
            ).fetchone()
            if asset is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Asset not found")
            if asset["status"] == "revoked":
                conn.rollback()
                return {"asset_id": asset_id, "status": "revoked", "cancelled_listings": 0}
            conn.execute(
                "UPDATE verified_assets SET status='revoked', revoked_at=? WHERE id=? AND status='verified'",
                (now, asset_id),
            )
            listings = conn.execute(
                select_for_update(conn, "SELECT id FROM marketplace_orders WHERE asset_id=? AND status='active'"),
                (asset_id,),
            ).fetchall()
            listing_ids = [row["id"] for row in listings]
            for listing_id in listing_ids:
                conn.execute(
                    "UPDATE marketplace_orders SET status='cancelled', updated_at=? WHERE id=? AND status='active'",
                    (now, listing_id),
                )
                conn.execute(
                    """INSERT INTO marketplace_order_events(order_id, actor_user_id, event_type, created_at)
                       VALUES (?, ?, 'cancelled', ?)""",
                    (listing_id, str(admin["id"]), now),
                )
            if listing_ids:
                placeholders = ",".join("?" for _ in listing_ids)
                conn.execute(
                    f"""UPDATE marketplace_offers SET status='declined', updated_at=?
                        WHERE order_id IN ({placeholders}) AND status='pending'""",
                    (now, *listing_ids),
                )
            marketplace.insert_security_audit_event(
                conn, actor_telegram_id=str(admin["id"]),
                action="admin_asset_revoked", target_type="verified_asset",
                target_id=asset_id,
                metadata={"cancelled_listings": len(listing_ids), "reason": "asset_moderation"},
                now=now,
            )
            conn.commit()
        return {"asset_id": asset_id, "status": "revoked", "cancelled_listings": len(listing_ids)}

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
                          d.created_at,
                          (SELECT COUNT(*) FROM marketplace_dispute_resolution_approvals a WHERE a.deal_id=d.id) AS approval_count,
                          (SELECT a.decision FROM marketplace_dispute_resolution_approvals a WHERE a.deal_id=d.id ORDER BY a.created_at DESC LIMIT 1) AS approved_decision
                   FROM marketplace_deals d""" + where +
                " ORDER BY d.created_at DESC LIMIT ?", (*values, limit)
            ).fetchall()
        return {"items": [dict(row) for row in rows]}

    return router
