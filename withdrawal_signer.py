"""Multi-approval, remote-HSM TON withdrawal execution.

No seed phrase or private key is accepted or stored by this service. Signing is
delegated to an independently operated HSM/KMS gateway. All uncertain broadcast
outcomes are quarantined and never retried automatically.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from typing import Any, Protocol

import httpx
from sqlalchemy import BigInteger, Column, ForeignKey, String, Table, Text, UniqueConstraint, insert, select
from sqlalchemy.engine import Engine

from financial_ledger import (
    metadata, get_engine, init_financial_schema, migration_revision_number, withdrawal_requests,
    advance_withdrawal_to_processing, mark_withdrawal_submitted,
    confirm_ton_withdrawal, quarantine_ton_withdrawal,
)
from asset_policy import ASSETS


withdrawal_approvals = Table(
    "withdrawal_approvals", metadata,
    Column("approval_id", String(36), primary_key=True),
    Column("request_id", String(36), ForeignKey("withdrawal_requests.request_id"), nullable=False),
    Column("admin_user_id", String(64), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    UniqueConstraint("request_id", "admin_user_id", name="uq_withdrawal_approval_admin"),
)
withdrawal_execution_events = Table(
    "withdrawal_execution_events", metadata,
    Column("event_id", String(36), primary_key=True),
    Column("request_id", String(36), ForeignKey("withdrawal_requests.request_id"), nullable=False),
    Column("event_type", String(40), nullable=False),
    Column("actor_id", String(64), nullable=False, default=""),
    Column("event_data", Text, nullable=False, default="{}"),
    Column("created_at", BigInteger, nullable=False),
)


def init_withdrawal_signer_schema(engine: Engine | None = None) -> None:
    db = engine or get_engine()
    init_financial_schema(db)
    if db.dialect.name == "postgresql":
        with db.connect() as conn:
            version = conn.execute(__import__("sqlalchemy").text(
                "SELECT version_num FROM alembic_version"
            )).scalar_one_or_none()
        if migration_revision_number(version) < 14:
            raise RuntimeError("Withdrawal signer schema migrations through 0014 are required")
        return
    metadata.create_all(db)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _approved_admin_ids() -> set[str]:
    return {part.strip() for part in os.getenv("WITHDRAWAL_APPROVER_IDS", "").split(",")
            if part.strip().isdigit()}


def _event(conn, *, request_id: str, event_type: str, actor_id: str = "",
           data: dict[str, Any] | None = None, now: int) -> None:
    conn.execute(insert(withdrawal_execution_events).values(
        event_id=str(uuid.uuid4()), request_id=request_id, event_type=event_type,
        actor_id=str(actor_id)[:64],
        event_data=json.dumps(data or {}, sort_keys=True, separators=(",", ":"))[:2000],
        created_at=now,
    ))


def approve_withdrawal(*, request_id: str, admin_user_id: str,
                       engine: Engine | None = None,
                       now: int | None = None) -> dict[str, Any]:
    """Collect two distinct pre-authorized administrator approvals for an exact request."""
    db = engine or get_engine()
    init_withdrawal_signer_schema(db)
    admin = str(admin_user_id)
    if admin not in _approved_admin_ids():
        raise PermissionError("Administrator is not in the withdrawal approval allowlist")
    current = int(now or time.time())
    lock = db.dialect.name == "postgresql"
    with db.begin() as conn:
        q = select(withdrawal_requests).where(withdrawal_requests.c.request_id == request_id)
        if lock:
            q = q.with_for_update()
        request = conn.execute(q).mappings().first()
        if request is None:
            raise ValueError("Unknown withdrawal request")
        if admin == request["user_id"]:
            raise PermissionError("Withdrawal owner cannot approve their own withdrawal")
        if request["status"] != "requested":
            raise ValueError("Only a newly requested withdrawal can be approved")
        request_hash = str(request["request_hash"])
        existing = conn.execute(select(withdrawal_approvals).where(
            withdrawal_approvals.c.request_id == request_id,
            withdrawal_approvals.c.admin_user_id == admin,
        )).mappings().first()
        if existing and existing["request_hash"] != request_hash:
            raise ValueError("Approval does not match the current withdrawal request")
        if not existing:
            conn.execute(insert(withdrawal_approvals).values(
                approval_id=str(uuid.uuid4()), request_id=request_id,
                admin_user_id=admin, request_hash=request_hash, created_at=current,
            ))
            _event(conn, request_id=request_id, event_type="approval_added",
                   actor_id=admin, data={"request_hash": request_hash}, now=current)
        approvals = conn.execute(select(withdrawal_approvals.c.admin_user_id).where(
            withdrawal_approvals.c.request_id == request_id,
            withdrawal_approvals.c.request_hash == request_hash,
        )).scalars().all()
        return {"request_id": request_id, "approved": True,
                "approval_count": len(set(approvals)), "required_approvals": 2,
                "ready": len(set(approvals)) >= 2}


class HsmSigner(Protocol):
    async def sign_withdrawal(self, request: dict[str, Any], approval_ids: list[str]) -> dict[str, str]:
        ...


class RemoteHsmSigner:
    """HTTPS signer gateway. It receives an intent, never an application-held private key."""

    def __init__(self, *, base_url: str, token: str, timeout: float = 20.0):
        if not base_url.startswith("https://") or not token:
            raise RuntimeError("A secure remote HSM signer URL and secret are required")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    async def sign_withdrawal(self, request: dict[str, Any], approval_ids: list[str]) -> dict[str, str]:
        payload = {
            "request_id": str(request["request_id"]),
            "request_hash": str(request["request_hash"]),
            "wallet_id": os.getenv("TON_SIGNER_WALLET_ID", "").strip(),
            "network": str(request["network"]),
            "asset_id": str(request["asset_id"]),
            "destination_address": str(request["destination_address"]),
            "amount_raw": str(request["amount_raw"]),
            "fee_raw": str(request["fee_raw"]),
            "approval_ids": sorted(approval_ids),
        }
        if not payload["wallet_id"] or len(payload["approval_ids"]) < 2:
            raise PermissionError("HSM wallet identity and two approvals are required")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.base_url + "/v1/ton/withdrawals/sign",
                json=payload,
                headers={"Authorization": "Bearer " + self.token,
                         "Idempotency-Key": str(request["request_id"])},
            )
        response.raise_for_status()
        data = response.json()
        if data.get("request_hash") != payload["request_hash"] or data.get("approved") is not True:
            raise PermissionError("Remote HSM did not approve the exact withdrawal intent")
        boc = str(data.get("signed_boc") or "")
        message_hash = str(data.get("message_hash") or "").lower().removeprefix("0x")
        try:
            decoded = base64.b64decode(boc, validate=True)
        except Exception:
            raise ValueError("Remote HSM returned an invalid signed BOC") from None
        if not decoded or len(decoded) > 65536 or len(message_hash) != 64:
            raise ValueError("Remote HSM returned an invalid signed message")
        if any(ch not in "0123456789abcdef" for ch in message_hash):
            raise ValueError("Remote HSM returned an invalid message hash")
        return {"signed_boc": boc, "message_hash": message_hash}


class ToncenterBroadcaster:
    def __init__(self, *, api_key: str, timeout: float = 20.0):
        if not api_key:
            raise RuntimeError("TONCENTER_API_KEY is required for withdrawal broadcasting")
        self.api_key = api_key
        self.timeout = timeout

    async def broadcast(self, signed_boc: str) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                "https://toncenter.com/api/v2/sendBoc",
                json={"boc": signed_boc},
                headers={"X-API-Key": self.api_key},
            )
        response.raise_for_status()
        data = response.json()
        if data.get("ok") is not True:
            raise RuntimeError("TON broadcaster did not accept the signed message")
        result = data.get("result") or {}
        message_hash = str(result.get("hash") or result.get("message_hash") or "").lower().removeprefix("0x")
        if len(message_hash) != 64 or any(ch not in "0123456789abcdef" for ch in message_hash):
            raise RuntimeError("TON broadcaster returned no canonical message hash")
        return message_hash

    async def resolve_final_transaction(self, message_hash: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=self.timeout, headers={"X-API-Key": self.api_key}) as client:
            response = await client.get(
                "https://toncenter.com/api/v3/transactionsByMessage",
                params={"msg_hash": message_hash, "direction": "in", "limit": 10},
            )
        response.raise_for_status()
        rows = response.json().get("transactions") or []
        for tx in rows:
            incoming = tx.get("in_msg") or {}
            candidate = str(incoming.get("hash_norm") or incoming.get("hash") or "").lower().removeprefix("0x")
            if candidate != message_hash:
                continue
            description = tx.get("description") or {}
            compute = description.get("compute_ph") or {}
            action = description.get("action") or {}
            if (int(tx.get("mc_block_seqno") or 0) > 0
                    and int(compute.get("exit_code", -1)) == 0
                    and int(action.get("result_code", -1)) == 0
                    and description.get("aborted") is not True):
                tx_hash = str(tx.get("hash") or "").lower().removeprefix("0x")
                if len(tx_hash) == 64 and all(ch in "0123456789abcdef" for ch in tx_hash):
                    return {"tx_hash": tx_hash, "finalized": True}
        return None


async def execute_approved_withdrawal(*, request_id: str,
                                      engine: Engine | None = None,
                                      signer: HsmSigner | None = None,
                                      broadcaster: ToncenterBroadcaster | None = None,
                                      now: int | None = None) -> dict[str, Any]:
    """Execute a two-approved withdrawal once; every ambiguous external result is quarantined."""
    if os.getenv("TON_WITHDRAWALS_ENABLED", "false").strip().lower() != "true":
        raise PermissionError("TON withdrawals are disabled by policy")
    if os.getenv("TON_WITHDRAWAL_EXECUTOR_ENABLED", "false").strip().lower() != "true":
        raise PermissionError("TON withdrawal executor is disabled")
    release_gates = (
        "KZ_LEGAL_REVIEW_APPROVED", "KZ_DATA_RESIDENCY_VERIFIED",
        "KZ_TAX_REVIEW_APPROVED", "KZ_SECURITY_REVIEW_APPROVED",
    )
    if not all(os.getenv(name, "false").strip().lower() == "true" for name in release_gates):
        raise PermissionError("Kazakhstan legal, data, tax and security sign-offs are required")
    db = engine or get_engine()
    init_withdrawal_signer_schema(db)
    current = int(now or time.time())
    with db.connect() as conn:
        request = conn.execute(select(withdrawal_requests).where(
            withdrawal_requests.c.request_id == request_id
        )).mappings().first()
        if request is None:
            raise ValueError("Unknown withdrawal request")
        request = dict(request)
        approvals = conn.execute(select(withdrawal_approvals).where(
            withdrawal_approvals.c.request_id == request_id,
            withdrawal_approvals.c.request_hash == request["request_hash"],
        )).mappings().all()
    if request["status"] != "requested" or len({row["admin_user_id"] for row in approvals}) < 2:
        raise PermissionError("Withdrawal needs two distinct administrator approvals")
    asset = ASSETS.get(request["asset_id"])
    if (asset is None or asset.chain.value != "ton-mainnet"
            or request["network"] != "ton-mainnet"):
        raise PermissionError("Unsupported withdrawal asset or network")
    selected_signer = signer or RemoteHsmSigner(
        base_url=os.getenv("TON_SIGNER_SERVICE_URL", ""),
        token=os.getenv("TON_SIGNER_SERVICE_TOKEN", ""),
    )
    selected_broadcaster = broadcaster or ToncenterBroadcaster(
        api_key=os.getenv("TONCENTER_API_KEY", "")
    )
    approval_ids = [str(row["approval_id"]) for row in approvals]
    advance_withdrawal_to_processing(request_id=request_id, engine=db, now=current)
    try:
        with db.begin() as conn:
            _event(conn, request_id=request_id, event_type="signing_requested",
                   data={"approval_count": len(approval_ids)}, now=current)
        signed = await selected_signer.sign_withdrawal(request, approval_ids)
        with db.begin() as conn:
            _event(conn, request_id=request_id, event_type="signed",
                   data={"message_hash": signed["message_hash"]}, now=int(time.time()))
        message_hash = await selected_broadcaster.broadcast(signed["signed_boc"])
        with db.begin() as conn:
            _event(conn, request_id=request_id, event_type="broadcast_accepted",
                   data={"message_hash": message_hash}, now=int(time.time()))
        transaction = await selected_broadcaster.resolve_final_transaction(message_hash)
        if transaction is None:
            quarantine_ton_withdrawal(
                request_id=request_id, reason="Broadcast accepted; final transaction is not yet independently confirmed",
                engine=db,
            )
            return {"status": "reconciliation_required", "request_id": request_id}
        mark_withdrawal_submitted(
            request_id=request_id, tx_hash=transaction["tx_hash"], engine=db,
        )
        result = confirm_ton_withdrawal(
            request_id=request_id, tx_hash=transaction["tx_hash"], engine=db,
        )
        with db.begin() as conn:
            _event(conn, request_id=request_id, event_type="confirmed",
                   data={"tx_hash": transaction["tx_hash"]}, now=int(time.time()))
        return {"status": "confirmed", "request_id": request_id, **result}
    except Exception as exc:
        try:
            quarantine_ton_withdrawal(
                request_id=request_id,
                reason="Signer or broadcast outcome is ambiguous (" + type(exc).__name__ + ")",
                engine=db,
            )
        except Exception:
            pass
        raise
