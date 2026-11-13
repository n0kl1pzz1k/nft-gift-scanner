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
    withdrawal_events, accounts, _post, _account_balance_in_connection,
    advance_withdrawal_to_processing, mark_withdrawal_submitted,
    confirm_ton_withdrawal, quarantine_ton_withdrawal,
)
from asset_policy import ASSETS, validate_payment_amount, is_crypto_payment_enabled
from release_policy import TON_TESTNET_WITHDRAWAL_ADAPTER_READY
from ton_nft_providers import canonical_ton_address


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


def create_withdrawal_request(*, user_id: str, asset_id: str,
                              destination_address: str, amount_raw: int,
                              idempotency_key: str, engine: Engine | None = None,
                              now: int | None = None) -> dict[str, Any]:
    """Reserve funds only when the dedicated TON testnet payout adapter exists."""
    if not TON_TESTNET_WITHDRAWAL_ADAPTER_READY:
        raise PermissionError("TON withdrawal requests are disabled: no testnet payout adapter is implemented")
    if os.getenv("TON_WITHDRAWAL_REQUESTS_ENABLED", "false").strip().lower() != "true":
        raise PermissionError("TON withdrawal requests are disabled by policy")
    if os.getenv("TON_WITHDRAWALS_ENABLED", "false").strip().lower() != "true":
        raise PermissionError("TON withdrawals are not approved for release")
    if os.getenv("TON_WITHDRAWAL_EXECUTOR_ENABLED", "false").strip().lower() != "true":
        raise PermissionError("The audited withdrawal executor is not enabled")
    if (not os.getenv("TON_SIGNER_SERVICE_URL", "").strip().startswith("https://")
            or not os.getenv("TON_SIGNER_SERVICE_TOKEN", "").strip()
            or not os.getenv("TON_SIGNER_WALLET_ID", "").strip()
            or not os.getenv("TONCENTER_API_KEY", "").strip()):
        raise PermissionError("The remote HSM signer and TON broadcaster are not fully configured")
    if len(_approved_admin_ids()) < 2:
        raise PermissionError("At least two distinct withdrawal approvers must be configured")
    release_gates = (
        "KZ_LEGAL_REVIEW_APPROVED", "KZ_DATA_RESIDENCY_VERIFIED",
        "KZ_TAX_REVIEW_APPROVED", "KZ_SECURITY_REVIEW_APPROVED",
    )
    if not all(os.getenv(name, "false").strip().lower() == "true" for name in release_gates):
        raise PermissionError("Kazakhstan legal, data, tax and security sign-offs are required")
    uid = str(user_id).strip()
    if not uid.isdigit() or int(uid) <= 0:
        raise ValueError("Invalid Telegram user ID")
    if not idempotency_key or len(idempotency_key) < 16 or len(idempotency_key) > 200:
        raise ValueError("A bounded withdrawal idempotency key is required")
    asset = ASSETS.get(asset_id)
    if asset is None or asset_id not in {"GRAM", "usdt-ton"}:
        raise ValueError("Unsupported TON withdrawal asset")
    if not is_crypto_payment_enabled(asset_id):
        raise PermissionError("The TON payment rail has not passed its operational release gates")
    try:
        amount = validate_payment_amount(asset_id, amount_raw)
        destination = canonical_ton_address(destination_address)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if not destination.startswith("0:"):
        raise ValueError("Only TON mainnet basechain withdrawal addresses are supported")
    fee_name = "TON_WITHDRAWAL_FEE_GRAM_RAW" if asset_id == "GRAM" else "TON_WITHDRAWAL_FEE_USDT_RAW"
    try:
        fee = int(os.getenv(fee_name, "0"))
    except ValueError:
        raise RuntimeError("Configured withdrawal fee is invalid") from None
    if fee < 0 or fee > 9_000_000_000_000_000:
        raise RuntimeError("Configured withdrawal fee is outside the supported range")

    current = int(now or time.time())
    request_payload = {
        "user_id": uid, "asset_id": asset_id, "network": "ton-mainnet",
        "destination_address": destination, "amount_raw": str(amount),
        "fee_raw": str(fee),
    }
    request_hash = _hash(request_payload)
    db = engine or get_engine()
    init_withdrawal_signer_schema(db)
    request_id = str(uuid.uuid4())
    user_account = f"liability:user:{uid}:{asset_id}"
    seller_account = f"liability:seller:{uid}:{asset_id}"
    hold_account = f"liability:withdrawal_hold:{request_id}:{asset_id}"
    with db.begin() as conn:
        existing = conn.execute(select(withdrawal_requests).where(
            withdrawal_requests.c.idempotency_key == idempotency_key
        )).mappings().first()
        if existing:
            if existing["request_hash"] != request_hash or existing["user_id"] != uid:
                raise ValueError("Withdrawal idempotency key was reused with different details")
            return {**dict(existing), "duplicate": True}

        if db.dialect.name == "postgresql":
            conn.execute(select(accounts.c.account_id).where(
                accounts.c.account_id.in_((user_account, seller_account))
            ).order_by(accounts.c.account_id).with_for_update()).all()
        # Recheck idempotency after account locks to serialize concurrent requests
        # from the same user before either one reserves the ledger balance.
        existing = conn.execute(select(withdrawal_requests).where(
            withdrawal_requests.c.idempotency_key == idempotency_key
        )).mappings().first()
        if existing:
            if existing["request_hash"] != request_hash or existing["user_id"] != uid:
                raise ValueError("Withdrawal idempotency key was reused with different details")
            return {**dict(existing), "duplicate": True}
        user_available = max(0, -_account_balance_in_connection(conn, user_account, asset_id))
        seller_available = max(0, -_account_balance_in_connection(conn, seller_account, asset_id))
        required = amount + fee
        if user_available + seller_available < required:
            raise PermissionError("Insufficient available ledger balance for withdrawal and fee")
        user_debit = min(user_available, required)
        seller_debit = required - user_debit
        conn.execute(insert(withdrawal_requests).values(
            request_id=request_id, user_id=uid, asset_id=asset_id,
            network="ton-mainnet", destination_address=destination,
            amount_raw=str(amount), fee_raw=str(fee), status="requested",
            idempotency_key=idempotency_key, request_hash=request_hash,
            tx_hash=None, failure_reason=None, created_at=current, updated_at=current,
        ))
        postings = []
        if user_debit:
            postings.append({
                "account_id": user_account, "account_type": "liability",
                "currency": asset_id, "amount_raw": user_debit,
            })
        if seller_debit:
            postings.append({
                "account_id": seller_account, "account_type": "liability",
                "currency": asset_id, "amount_raw": seller_debit,
            })
        postings.append({
            "account_id": hold_account, "account_type": "liability",
            "currency": asset_id, "amount_raw": -amount,
        })
        if fee:
            postings.append({
                "account_id": f"revenue:withdrawal_fee:{asset_id}",
                "account_type": "revenue", "currency": asset_id,
                "amount_raw": -fee,
            })
        journal = _post(
            conn, idempotency_key="withdrawal-reserve:" + request_id,
            source_type="withdrawal_reserve", source_reference=request_id,
            postings=postings, now=current,
        )
        event_data = json.dumps({
            "asset_id": asset_id, "amount_raw": amount, "fee_raw": fee,
            "request_hash": request_hash, "journal_id": journal["entry_id"],
        }, sort_keys=True, separators=(",", ":"))
        conn.execute(insert(withdrawal_events).values(
            event_id=str(uuid.uuid4()), request_id=request_id, actor_type="user",
            actor_id=uid, event_type="requested", event_data=event_data[:1000],
            created_at=current,
        ))
        _event(conn, request_id=request_id, event_type="requested", actor_id=uid,
               data={"request_hash": request_hash, "journal_id": journal["entry_id"]},
               now=current)
    return {
        "request_id": request_id, "user_id": uid, "asset_id": asset_id,
        "network": "ton-mainnet", "destination_address": destination,
        "amount_raw": amount, "fee_raw": fee, "status": "requested",
        "request_hash": request_hash, "duplicate": False,
    }


def _require_matching_message_hash(signed_hash: str, broadcast_hash: str) -> str:
    """Bind the broadcaster acknowledgement to the exact HSM-signed message."""
    def canonical(value: str) -> str:
        result = str(value or "").strip().lower().removeprefix("0x")
        if len(result) != 64 or any(ch not in "0123456789abcdef" for ch in result):
            raise ValueError("TON message hash is not canonical")
        return result

    signed = canonical(signed_hash)
    broadcast = canonical(broadcast_hash)
    if signed != broadcast:
        raise ValueError("Broadcaster acknowledgement does not match the HSM-signed message")
    return broadcast


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
    """Execute a withdrawal only after a testnet broadcaster has been implemented."""
    if not TON_TESTNET_WITHDRAWAL_ADAPTER_READY:
        raise PermissionError("TON withdrawals are disabled: the current signer and broadcaster target mainnet")
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
        # Do not resolve or confirm a different message than the HSM approved.
        message_hash = _require_matching_message_hash(signed["message_hash"], message_hash)
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
