import os
import asyncio
import time
import json
import hmac
import hashlib
import secrets
from urllib.parse import parse_qsl
from pydantic import BaseModel

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware

from marketplace import create_marketplace_router, consume_telegram_init_data, create_telegram_session, get_telegram_session_user, init_marketplace_db, upsert_telegram_user, revoke_telegram_session, revoke_all_telegram_sessions, list_telegram_sessions, claim_telegram_update, finish_telegram_update, release_telegram_update, _enforce_user_rate_limit, sync_ton_nft_assets, sync_telegram_gift_assets
from ton_connect import init_ton_connect_db, create_proof_challenge, verify_ton_proof, consume_challenge_and_link_wallet, list_verified_wallets
from ton_nft_inventory import fetch_wallet_nfts
from telegram_gift_inventory import fetch_user_telegram_gifts
import httpx
from demo_offers import create_demo_offers_router, init_demo_offers_db

app = FastAPI(title="HASSLE NFT Market API", version="1.0.0")
app.include_router(create_marketplace_router(
    lambda value: _authenticate_telegram_credential(value),
    lambda value: _require_admin(value),
))
app.include_router(create_demo_offers_router(lambda value: _authenticate_telegram_credential(value)))

_telegram_bot = None
_telegram_dispatcher = None
_telegram_webhook_secret = None

# Browser access is restricted to the production Mini App origins.
# Extra origins may be supplied explicitly for a controlled preview environment.
_CORS_DEFAULT_ORIGINS = [
    "https://hassle-nft-market.fj7kmzkhn9.workers.dev",
]
_CORS_ALLOWED_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", ",".join(_CORS_DEFAULT_ORIGINS)).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Telegram-Init-Data"],
    max_age=600,
)

class TelegramAuthRequest(BaseModel):
    init_data: str


class TonProofDomain(BaseModel):
    lengthBytes: int
    value: str


class TonProofData(BaseModel):
    timestamp: int
    domain: TonProofDomain
    signature: str
    payload: str


class TonProofVerifyRequest(BaseModel):
    address: str
    network: str
    public_key: str | None = None
    proof: TonProofData
    state_init: str


def _admin_ids() -> set[str]:
    raw = os.getenv("ADMIN_TELEGRAM_IDS", os.getenv("ADMIN_TELEGRAM_ID", ""))
    return {part.strip() for part in raw.split(",") if part.strip().isdigit()}


def _verify_telegram_init_data(init_data: str) -> dict:
    """Verify raw Telegram WebApp initData using Telegram's documented HMAC scheme.

    Raw initData is accepted only by the session-exchange endpoint. API requests
    should use the short-lived opaque session token issued after this check.
    """
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise HTTPException(status_code=503, detail="Telegram authentication is not configured")
    if not isinstance(init_data, str) or not init_data or len(init_data) > 8192:
        raise HTTPException(status_code=401, detail="Missing or invalid Telegram initData")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True, max_num_fields=64)
        values: dict[str, str] = {}
        for key, value in pairs:
            if not key or key in values:
                raise ValueError("Duplicate or empty initData field")
            values[key] = value
        received_hash = values.pop("hash", "")
        if len(received_hash) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in received_hash):
            raise ValueError("Invalid Telegram hash")
        check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash.lower()):
            raise ValueError("Invalid Telegram signature")

        auth_date_raw = values.get("auth_date", "")
        if not auth_date_raw.isdigit():
            raise ValueError("Invalid auth_date")
        auth_date = int(auth_date_raw)
        now = int(time.time())
        max_age = max(60, min(int(os.getenv("TELEGRAM_INITDATA_MAX_AGE_SECONDS", "300")), 900))
        if auth_date <= 0 or auth_date > now + 30 or now - auth_date > max_age:
            raise ValueError("Expired Telegram initData")

        user_raw = values.get("user", "")
        user = json.loads(user_raw)
        if not isinstance(user, dict):
            raise ValueError("Telegram user is missing")
        user_id = user.get("id")
        if isinstance(user_id, bool) or not isinstance(user_id, (int, str)) or not str(user_id).isdigit() or int(user_id) <= 0:
            raise ValueError("Telegram user ID is invalid")
        return user
    except (ValueError, TypeError, json.JSONDecodeError, OverflowError):
        raise HTTPException(status_code=401, detail="Telegram authentication failed; reopen the Mini App")


def _authenticate_telegram_credential(credential: str) -> dict:
    """Resolve only an opaque server-issued session; raw initData is never an API credential."""
    if not credential or not credential.startswith("grs_"):
        raise HTTPException(status_code=401, detail="A server-issued Telegram session is required")
    user = get_telegram_session_user(credential)
    if user is None:
        raise HTTPException(status_code=401, detail="Telegram session expired; reopen the Mini App")
    return user


def _require_admin(credential: str | None) -> dict:
    if not credential:
        raise HTTPException(status_code=401, detail="Telegram Mini App authentication required")
    user = _authenticate_telegram_credential(credential)
    if str(user["id"]) not in _admin_ids():
        raise HTTPException(status_code=403, detail="Administrator access only")
    return user


@app.post("/api/auth/telegram")
async def telegram_auth(payload: TelegramAuthRequest):
    user = _verify_telegram_init_data(payload.init_data)
    if not consume_telegram_init_data(payload.init_data, str(user["id"])):
        raise HTTPException(status_code=409, detail="This Telegram initData was already exchanged; reopen the Mini App")
    profile = upsert_telegram_user(user)
    session_token = create_telegram_session(str(user["id"]))
    is_admin = str(user["id"]) in _admin_ids()
    return {
        "authenticated": True,
        "is_admin": is_admin,
        "session_token": session_token,
        "expires_in": 43200,
        "user": profile,
    }

@app.get("/api/auth/me")
async def telegram_me(x_telegram_init_data: str | None = Header(default=None)):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    return {
        "authenticated": True,
        "is_admin": str(user["id"]) in _admin_ids(),
        "user": {
            "id": str(user["id"]),
            "first_name": user.get("first_name") or "Telegram user",
            "last_name": user.get("last_name") or "",
            "username": user.get("username") or "",
            "photo_url": user.get("photo_url") or "",
            "is_premium": bool(user.get("is_premium")),
        },
    }


@app.get("/api/auth/sessions")
async def telegram_sessions(x_telegram_init_data: str | None = Header(default=None)):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    return {"items": list_telegram_sessions(str(user["id"]), x_telegram_init_data)}


@app.post("/api/auth/logout")
async def telegram_logout(x_telegram_init_data: str | None = Header(default=None)):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    revoked = revoke_telegram_session(x_telegram_init_data or "")
    return {"authenticated": False, "revoked": revoked, "user_id": str(user["id"])}


@app.post("/api/auth/logout-all")
async def telegram_logout_all(x_telegram_init_data: str | None = Header(default=None)):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    revoked_count = revoke_all_telegram_sessions(str(user["id"]))
    return {"revoked_sessions": revoked_count}


@app.post("/api/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if (
        _telegram_bot is None
        or _telegram_dispatcher is None
        or not _telegram_webhook_secret
        or not x_telegram_bot_api_secret_token
        or not hmac.compare_digest(x_telegram_bot_api_secret_token, _telegram_webhook_secret)
    ):
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > 1_048_576:
                raise HTTPException(status_code=413, detail="Telegram update is too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid content length")
    raw_body = await request.body()
    if len(raw_body) > 1_048_576:
        raise HTTPException(status_code=413, detail="Telegram update is too large")
    try:
        payload = json.loads(raw_body)
        if not isinstance(payload, dict):
            raise ValueError("Update must be an object")
        update_id = payload.get("update_id")
        if isinstance(update_id, bool) or not isinstance(update_id, int) or update_id < 0:
            raise ValueError("Invalid update_id")
    except (ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Invalid Telegram update")

    claim = claim_telegram_update(update_id, payload)
    if claim == "conflict":
        raise HTTPException(status_code=409, detail="Telegram update ID payload conflict")
    if claim == "processed":
        return {"ok": True, "duplicate": True}
    if claim == "in_progress":
        raise HTTPException(status_code=503, detail="Telegram update is already being processed", headers={"Retry-After": "2"})

    from aiogram.types import Update
    try:
        update = Update.model_validate(payload, context={"bot": _telegram_bot})
        await _telegram_dispatcher.feed_update(_telegram_bot, update)
    except Exception:
        release_telegram_update(update_id)
        raise HTTPException(status_code=500, detail="Telegram update processing failed")
    finish_telegram_update(update_id)
    return {"ok": True, "duplicate": False}

@app.post("/api/ton-proof/payload")
async def ton_proof_payload(x_telegram_init_data: str | None = Header(default=None)):
    credential = x_telegram_init_data or ""
    user = _authenticate_telegram_credential(credential)
    try:
        payload = create_proof_challenge(str(user["id"]), credential)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return {"payload": payload, "expires_in": 300}


@app.post("/api/ton-proof/verify")
async def ton_proof_verify(
    payload: TonProofVerifyRequest,
    x_telegram_init_data: str | None = Header(default=None),
):
    credential = x_telegram_init_data or ""
    user = _authenticate_telegram_credential(credential)
    expected_domain = os.getenv(
        "TON_CONNECT_DOMAIN",
        "hassle-nft-market.fj7kmzkhn9.workers.dev",
    ).strip()
    try:
        verified = verify_ton_proof(
            address=payload.address,
            network=payload.network,
            public_key_hex=payload.public_key,
            proof=payload.proof.model_dump(),
            state_init_b64=payload.state_init,
            expected_domain=expected_domain,
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    if not consume_challenge_and_link_wallet(
        verified,
        str(user["id"]),
        credential,
    ):
        raise HTTPException(status_code=409, detail="TON proof challenge expired, mismatched, or already used")
    return {
        "verified": True,
        "address": verified["address"],
        "network": verified["network"],
    }


@app.get("/api/ton-proof/wallets/me")
async def ton_proof_wallets_me(x_telegram_init_data: str | None = Header(default=None)):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    return {"items": list_verified_wallets(str(user["id"]))}


@app.post("/api/marketplace/assets/import-ton")
async def import_ton_wallet_assets(x_telegram_init_data: str | None = Header(default=None)):
    credential = x_telegram_init_data or ""
    user = _authenticate_telegram_credential(credential)
    telegram_id = str(user["id"])
    _enforce_user_rate_limit(telegram_id, "import_ton_nfts", 3, 60)
    wallets = [
        wallet for wallet in list_verified_wallets(telegram_id)
        if wallet.get("network") == "-239"
    ]
    if not wallets:
        raise HTTPException(status_code=400, detail="Connect and verify a TON mainnet wallet first")

    snapshots = []
    try:
        for index, wallet in enumerate(wallets):
            if index and not os.getenv("TONAPI_TOKEN", "").strip():
                await asyncio.sleep(4.1)
            snapshot = await fetch_wallet_nfts(wallet["address"])
            snapshots.append(snapshot)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="TON NFT indexer is temporarily unavailable; no ownership records were changed",
            headers={"Retry-After": "30"},
        ) from exc

    all_items = [item for snapshot in snapshots for item in snapshot["items"]]
    complete = all(snapshot["complete"] for snapshot in snapshots)
    try:
        registered = sync_ton_nft_assets(
            owner_telegram_id=telegram_id,
            wallets=wallets,
            nft_items=all_items,
            complete=complete,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "imported": len(registered),
        "items": registered,
        "ownership_scan_complete": complete,
        "wallets_scanned": len(snapshots),
    }


@app.post("/api/marketplace/assets/import-telegram")
async def import_telegram_gifts(x_telegram_init_data: str | None = Header(default=None)):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    telegram_id = str(user["id"])
    _enforce_user_rate_limit(telegram_id, "import_telegram_gifts", 3, 60)
    try:
        snapshot = await fetch_user_telegram_gifts(telegram_id)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Telegram gift inventory is temporarily unavailable; no ownership records were changed",
            headers={"Retry-After": "30"},
        ) from exc
    try:
        registered = sync_telegram_gift_assets(
            owner_telegram_id=telegram_id,
            gifts=snapshot["items"],
            complete=snapshot["complete"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "imported": len(registered),
        "items": registered,
        "ownership_scan_complete": snapshot["complete"],
        "telegram_gifts_seen": snapshot["total_count"],
        "supported_unique_gifts": snapshot["supported_unique_count"],
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "hassle-nft-market"}


@app.on_event("startup")
async def start_services():
    global _telegram_bot, _telegram_dispatcher, _telegram_webhook_secret
    init_marketplace_db()
    init_demo_offers_db()
    init_ton_connect_db()
    token = os.getenv("BOT_TOKEN", "").strip()
    if token:
        try:
            from bot import create_bot, create_dispatcher
            _telegram_bot = create_bot()
            _telegram_dispatcher = create_dispatcher()
            _telegram_webhook_secret = os.getenv("BOT_WEBHOOK_SECRET", "").strip() or secrets.token_urlsafe(32)
            backend_url = os.getenv(
                "API_BASE_URL",
                "https://hassle-nft-market.up.railway.app",
            ).rstrip("/")
            await _telegram_bot.set_webhook(
                url=f"{backend_url}/api/telegram/webhook",
                secret_token=_telegram_webhook_secret,
                allowed_updates=_telegram_dispatcher.resolve_used_update_types(),
            )
        except Exception:
            import logging
            logging.exception("Could not configure Telegram webhook")


@app.on_event("shutdown")
async def stop_services():
    global _telegram_bot
    if _telegram_bot is not None:
        await _telegram_bot.session.close()
        _telegram_bot = None

