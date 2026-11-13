import os
import asyncio
import time
import json
import hmac
import hashlib
import secrets
import logging
from urllib.parse import parse_qsl
from pydantic import BaseModel, Field
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from marketplace import create_marketplace_router, consume_telegram_init_data, create_telegram_session, get_telegram_session_user, init_marketplace_db, upsert_telegram_user, revoke_telegram_session, revoke_all_telegram_sessions, list_telegram_sessions, claim_telegram_update, finish_telegram_update, release_telegram_update
from demo_offers import create_demo_offers_router, init_demo_offers_db
from security_controls import SecurityControlsMiddleware
from financial_ledger import get_engine
from marketplace_inventory import (
    save_verified_wallet, get_active_verified_wallet, revoke_verified_wallet, record_wallet_owned_nft,
    create_escrow_pending_listing, cancel_seller_listing, marketplace_assets, marketplace_listings,
    init_inventory_schema, scan_pending_nft_listings,
)
from ton_nft_verifier import verify_seller_nft_ownership_async
from ton_nft_providers import configured_nft_providers, canonical_ton_address
from sqlalchemy import select, func, or_

from financial_ledger import (
    create_telegram_stars_invoice, record_telegram_stars_terms_acceptance,
    withdrawal_requests,
    request_telegram_stars_refund, prepare_telegram_stars_refund,
    complete_telegram_stars_refund, fail_telegram_stars_refund,
    require_telegram_stars_refund_reconciliation,
)

class TonNftImportRequest(BaseModel):
    asset_type: str
    collection_address: str = Field(min_length=3, max_length=128)
    token_id: str = Field(min_length=1, max_length=40)
    item_address: str = Field(min_length=3, max_length=128)


class NativeListingRequest(BaseModel):
    asset_id: str = Field(min_length=4, max_length=64)
    price_raw: int = Field(gt=0, le=9_000_000_000_000_000)
    currency: str
    idempotency_key: str = Field(min_length=16, max_length=128)


app = FastAPI(title="GIFT RADAR API", version="0.4.0", docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(RequestValidationError)
async def generic_validation_error_handler(request: Request, exc: RequestValidationError):
    # Never echo rejected request values, credentials or detailed internal field paths.
    return JSONResponse(status_code=422, content={"detail": "Invalid request"})
app.include_router(create_marketplace_router(
    lambda value: _authenticate_telegram_credential(value),
    lambda value: _require_admin(value),
))
app.include_router(create_demo_offers_router(lambda value: _authenticate_telegram_credential(value)))

_telegram_bot = None
_telegram_dispatcher = None
_telegram_webhook_secret = None

def _stars_payment_enabled() -> bool:
    database_url = os.getenv("DATABASE_URL", "").strip()
    return bool(
        os.getenv("TELEGRAM_STARS_PAYMENTS_ENABLED", "false").strip().lower() == "true"
        and os.getenv("MARKETPLACE_REAL_DEALS_ENABLED", "false").strip().lower() == "true"
        and bool(os.getenv("TELEGRAM_STARS_TERMS_VERSION", "").strip())
        and _telegram_bot is not None
        and os.getenv("GIFT_RADAR_STORAGE_BACKEND", "sqlite").strip().lower() == "postgres"
        and database_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://"))
        and os.getenv("TERMS_URL", "").strip().startswith("https://")
        and os.getenv("PAYMENT_SUPPORT_URL", "").strip().startswith("https://")
        and os.getenv("KZ_LEGAL_REVIEW_APPROVED", "false").strip().lower() == "true"
        and os.getenv("KZ_DATA_RESIDENCY_VERIFIED", "false").strip().lower() == "true"
        and os.getenv("KZ_TAX_REVIEW_APPROVED", "false").strip().lower() == "true"
        and os.getenv("KZ_SECURITY_REVIEW_APPROVED", "false").strip().lower() == "true"
    )


@app.get("/api/payments/capabilities")
async def payment_capabilities():
    """Expose only payment methods that have passed their server-side release gates."""
    from asset_policy import ASSETS, is_crypto_payment_enabled

    crypto = {}
    for asset_id in ("GRAM", "usdt-ton", "usdt-tron"):
        asset = ASSETS[asset_id]
        enabled = is_crypto_payment_enabled(asset_id)
        crypto[asset_id] = {
            "enabled": enabled,
            "symbol": asset.display_symbol,
            "message": None if enabled else "Временно недоступно: проверка платёжного контура и разрешение на запуск не завершены.",
        }
    stars_enabled = _stars_payment_enabled()
    return {
        "stars": {
            "enabled": stars_enabled,
            "message": None if stars_enabled else "Оплата Telegram Stars временно недоступна.",
        },
        "crypto": crypto,
    }


# Browser access is restricted to the production Mini App origins.
# Extra origins may be supplied explicitly for a controlled preview environment.
_CORS_DEFAULT_ORIGINS = [
    "https://nft-gift-scanner.fj7kmzkhn9.workers.dev",
    "https://nft-gift-scanner.pages.dev",
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
app.add_middleware(SecurityControlsMiddleware)

_refresh_task: asyncio.Task | None = None
_inventory_task: asyncio.Task | None = None
_deposit_task: asyncio.Task | None = None
_deal_expiry_task: asyncio.Task | None = None


def _safe_total_count(reported_total: Any, item_count: int) -> int:
    """Never report fewer catalogue rows than are actually returned."""
    try:
        return max(item_count, int(reported_total or 0))
    except (TypeError, ValueError):
        return item_count


def _refresh_interval_seconds() -> int:
    try:
        return max(60, min(int(os.getenv("DATA_REFRESH_SECONDS", "60")), 900))
    except (TypeError, ValueError):
        return 60


class TelegramAuthRequest(BaseModel):
    init_data: str


class MarketplaceSettingRequest(BaseModel):
    enabled: bool


class StarsTermsAcceptanceRequest(BaseModel):
    accepted: bool


class StarsRefundRequestBody(BaseModel):
    invoice_id: str
    reason: str = ""


class TonProofVerifyRequest(BaseModel):
    address: str
    network: str
    public_key: str | None = None
    state_init: str
    proof: dict[str, Any]


# Admin controls are limited to catalogue visibility; the setting is persisted in SQLite.
# Administrator identities are verified server-side from Telegram initData and env configuration.
def _marketplace_visibility() -> dict[str, bool]:
    # GIFT RADAR is the marketplace. Third-party marketplace feeds are disabled.
    return {"native": True, "external_marketplaces": False}


def _marketplace_enabled(market: str) -> bool:
    # Legacy adapter switch is permanently fail-closed during removal.
    return False


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

@app.get("/api/auth/sessions")
async def telegram_sessions(x_telegram_init_data: str | None = Header(default=None)):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    return {"items": list_telegram_sessions(str(user["id"]), x_telegram_init_data)}


@app.post("/api/auth/logout")
async def telegram_logout(x_telegram_init_data: str | None = Header(default=None)):
    _authenticate_telegram_credential(x_telegram_init_data or "")
    revoked = revoke_telegram_session(x_telegram_init_data or "")
    return {"authenticated": False, "revoked": revoked}


@app.post("/api/auth/logout-all")
async def telegram_logout_all(x_telegram_init_data: str | None = Header(default=None)):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    revoked_count = revoke_all_telegram_sessions(str(user["id"]))
    return {"revoked_sessions": revoked_count}


@app.post("/api/admin/marketplaces/{market}", include_in_schema=False)
async def admin_set_marketplace(
    market: str,
    payload: MarketplaceSettingRequest,
    x_telegram_init_data: str | None = Header(default=None),
):
    _require_admin(x_telegram_init_data)
    raise HTTPException(status_code=410, detail="External marketplace adapters are permanently disabled")


@app.get("/api/admin/overview")
async def admin_overview(x_telegram_init_data: str | None = Header(default=None)):
    user = _require_admin(x_telegram_init_data)
    return {
        "admin": {"id": str(user["id"]), "first_name": user.get("first_name", ""), "username": user.get("username", "")},
        "mode": "independent_native_marketplace",
        "sources": source_status(),
        "marketplace_visibility": _marketplace_visibility(),
        "settings_persistence": "persistent_sqlite",
    }


def source_status() -> dict[str, str]:
    return {
        "native_inventory": "awaiting_native_inventory_index",
        "external_marketplaces": "disabled_by_product_policy",
    }


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

@app.post("/api/wallet/ton-proof/payload")
async def ton_proof_payload(x_telegram_init_data: str | None = Header(default=None)):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    service_url = os.getenv("TON_PROOF_SERVICE_URL", "").strip().rstrip("/")
    internal_token = os.getenv("TON_PROOF_INTERNAL_TOKEN", "").strip()
    if not service_url or not internal_token:
        raise HTTPException(status_code=503, detail="TON Proof service is not configured")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{service_url}/v1/challenges",
                headers={"X-Gift-Radar-Internal": internal_token},
                json={"userId": str(user["id"])},
            )
        if response.status_code != 200:
            raise HTTPException(status_code=503, detail="TON Proof challenge service is unavailable")
        result = response.json()
        if not isinstance(result, dict) or not isinstance(result.get("payload"), str):
            raise HTTPException(status_code=502, detail="TON Proof service returned an invalid challenge")
        return {"payload": result["payload"], "expires_at": result.get("expiresAt")}
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="TON Proof service is unreachable")


@app.post("/api/wallet/ton-proof/verify")
async def ton_proof_verify(
    payload: TonProofVerifyRequest,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    service_url = os.getenv("TON_PROOF_SERVICE_URL", "").strip().rstrip("/")
    internal_token = os.getenv("TON_PROOF_INTERNAL_TOKEN", "").strip()
    if not service_url or not internal_token:
        raise HTTPException(status_code=503, detail="TON Proof service is not configured")
    if payload.network not in {"-239", "-3"}:
        raise HTTPException(status_code=422, detail="Unsupported TON network")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{service_url}/v1/verify",
                headers={"X-Gift-Radar-Internal": internal_token},
                json={
                    "userId": str(user["id"]),
                    "address": payload.address,
                    "network": payload.network,
                    "public_key": payload.public_key,
                    "state_init": payload.state_init,
                    "proof": payload.proof,
                },
            )
        if response.status_code == 401:
            raise HTTPException(status_code=401, detail="TON wallet proof was rejected")
        if response.status_code == 409:
            raise HTTPException(status_code=409, detail="TON wallet proof challenge expired or was already used")
        if response.status_code != 200:
            raise HTTPException(status_code=503, detail="TON Proof verification service is unavailable")
        result = response.json()
        if not isinstance(result, dict) or result.get("verified") is not True:
            raise HTTPException(status_code=401, detail="TON wallet proof was not verified")
        if result.get("network") == "-239":
            save_verified_wallet(user_id=str(user["id"]), address=result.get("address", ""),
                                 network="ton-mainnet", engine=get_engine())
        return {"verified": True, "address": result.get("address"), "network": result.get("network")}
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="TON Proof service is unreachable")



@app.post("/api/wallet/ton-proof/revoke")
async def revoke_ton_wallet_proof(x_telegram_init_data: str | None = Header(default=None)):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    revoked = revoke_verified_wallet(user_id=str(user["id"]), engine=get_engine())
    return {"revoked": revoked > 0, "revoked_bindings": revoked}


@app.post("/api/inventory/nft/import")
async def import_owned_nft(
    payload: TonNftImportRequest,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    if payload.asset_type not in {"ton_nft", "collectible_username", "collectible_phone_number"}:
        raise HTTPException(status_code=422, detail="Unsupported collectible asset type")
    try:
        wallet = get_active_verified_wallet(user_id=str(user["id"]), engine=get_engine())
        registry = json.loads(os.getenv("GIFT_RADAR_NFT_COLLECTIONS", "{}"))
        allowed = {
            key: {canonical_ton_address(value) for value in values}
            for key, values in registry.items() if isinstance(values, list)
        }
        providers = configured_nft_providers()
        evidence = await verify_seller_nft_ownership_async(
            seller_user_id=str(user["id"]), wallet_user_id=str(user["id"]),
            verified_wallet_address=wallet, asset_type=payload.asset_type,
            network="ton-mainnet", collection_address=payload.collection_address,
            token_id=payload.token_id, item_address=payload.item_address,
            allowed_collections=allowed, providers=providers,
        )
        result = record_wallet_owned_nft(
            user_id=str(user["id"]), wallet_address=wallet,
            asset_type=payload.asset_type,
            collection_address=evidence["collection_address"],
            token_id=evidence["token_id"], item_address=evidence["item_address"],
            evidence=evidence, engine=get_engine(),
        )
        return {"item": result, "next_step": "Create a listing, then transfer the NFT to the official escrow address"}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="TON NFT verification providers are not configured") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        logging.warning("TON NFT provider unavailable: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="TON NFT verification providers are temporarily unavailable") from exc


@app.post("/api/inventory/listings", status_code=201)
async def create_native_inventory_listing(
    payload: NativeListingRequest,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    if payload.currency not in {"XTR", "GRAM", "USDT"}:
        raise HTTPException(status_code=422, detail="Unsupported listing currency")
    try:
        result = create_escrow_pending_listing(
            user_id=str(user["id"]), asset_id=payload.asset_id,
            price_raw=payload.price_raw, currency=payload.currency,
            idempotency_key=payload.idempotency_key, engine=get_engine(),
        )
        return {"listing": result, "public": False}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc



@app.post("/api/inventory/listings/{listing_id}/cancel")
async def cancel_native_inventory_listing(
    listing_id: str,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    try:
        return cancel_seller_listing(
            listing_id=listing_id, seller_user_id=str(user["id"]), engine=get_engine(),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/inventory/listings")
async def list_verified_inventory_listings(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, min_length=1, max_length=80),
    currency: str | None = Query(default=None, pattern="^(XTR|GRAM|USDT)$"),
    asset_type: str | None = Query(default=None, max_length=40),
):
    db = get_engine()
    init_inventory_schema(db)
    filters = [
        marketplace_listings.c.status == "active",
        marketplace_assets.c.custody_state == "escrowed",
    ]
    if currency:
        filters.append(marketplace_listings.c.currency == currency)
    if asset_type:
        filters.append(marketplace_assets.c.asset_type == asset_type)
    if search:
        term = "%" + search.strip().replace("%", "\\%").replace("_", "\\_") + "%"
        filters.append(or_(
            marketplace_assets.c.token_id.ilike(term, escape="\\"),
            marketplace_assets.c.collection_address.ilike(term, escape="\\"),
            marketplace_listings.c.listing_id.ilike(term, escape="\\"),
        ))
    join_from = marketplace_listings.join(
        marketplace_assets, marketplace_assets.c.asset_id == marketplace_listings.c.asset_id
    )
    with db.connect() as conn:
        total = conn.execute(select(func.count()).select_from(join_from).where(*filters)).scalar_one()
        rows = conn.execute(select(
            marketplace_listings.c.listing_id, marketplace_listings.c.price_raw,
            marketplace_listings.c.currency, marketplace_listings.c.created_at,
            marketplace_assets.c.asset_type, marketplace_assets.c.collection_address,
            marketplace_assets.c.token_id,
        ).select_from(join_from).where(*filters)
          .order_by(marketplace_listings.c.created_at.desc(), marketplace_listings.c.listing_id)
          .limit(limit).offset(offset)).mappings().all()
    return {"items": [dict(row) for row in rows], "total": int(total),
            "limit": limit, "offset": offset}


@app.post("/api/wallet/withdrawals", status_code=503)
async def create_wallet_withdrawal(
    x_telegram_init_data: str | None = Header(default=None),
):
    _authenticate_telegram_credential(x_telegram_init_data or "")
    # No audited signer, broadcaster or reconciliation worker exists yet.
    # Do not accept requests or reserve user funds until that complete path is reviewed.
    raise HTTPException(status_code=503, detail="TON withdrawals are temporarily unavailable")


@app.get("/api/wallet/withdrawals")
async def list_wallet_withdrawals(
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    db = get_engine()
    from sqlalchemy import select as sa_select
    with db.connect() as conn:
        rows = conn.execute(sa_select(
            withdrawal_requests.c.request_id, withdrawal_requests.c.asset_id,
            withdrawal_requests.c.network, withdrawal_requests.c.destination_address,
            withdrawal_requests.c.amount_raw, withdrawal_requests.c.fee_raw,
            withdrawal_requests.c.status, withdrawal_requests.c.tx_hash,
            withdrawal_requests.c.created_at,
            withdrawal_requests.c.updated_at,
        ).where(
            withdrawal_requests.c.user_id == str(user["id"])
        ).order_by(withdrawal_requests.c.created_at.desc()).limit(100)).mappings().all()
    return {"items": [dict(row) for row in rows]}


@app.post("/api/inventory/listings/{listing_id}/reserve")
async def reserve_native_listing(
    listing_id: str,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    from marketplace_inventory import reserve_verified_listing
    try:
        return reserve_verified_listing(
            listing_id=listing_id, buyer_user_id=str(user["id"]), engine=get_engine()
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Listing cannot be reserved")
    except ValueError:
        raise HTTPException(status_code=409, detail="Listing is unavailable")


@app.post("/api/inventory/reservations/{reservation_id}/deal")
async def create_native_marketplace_deal(
    reservation_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128),
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    try:
        wallet = get_active_verified_wallet(user_id=str(user["id"]), engine=get_engine())
        from marketplace_settlement import create_deal_from_reservation
        return create_deal_from_reservation(
            reservation_id=reservation_id, buyer_user_id=str(user["id"]),
            buyer_wallet_address=wallet, idempotency_key=idempotency_key,
            engine=get_engine(),
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="A verified buyer wallet and active reservation are required")
    except ValueError:
        raise HTTPException(status_code=409, detail="Reservation cannot become a deal")


@app.get("/api/inventory/deals/{deal_id}")
async def get_native_marketplace_deal(
    deal_id: str,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    from marketplace_settlement import marketplace_deals
    with get_engine().connect() as conn:
        row = conn.execute(select(
            marketplace_deals.c.deal_id, marketplace_deals.c.listing_id,
            marketplace_deals.c.asset_id, marketplace_deals.c.buyer_user_id,
            marketplace_deals.c.seller_user_id, marketplace_deals.c.currency,
            marketplace_deals.c.price_raw, marketplace_deals.c.status,
            marketplace_deals.c.created_at, marketplace_deals.c.updated_at,
        ).where(marketplace_deals.c.deal_id == deal_id)).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Deal not found")
    if str(user["id"]) not in {row["buyer_user_id"], row["seller_user_id"]}:
        raise HTTPException(status_code=404, detail="Deal not found")
    return dict(row)


@app.post("/api/admin/withdrawals/{request_id}/approve")
async def approve_admin_withdrawal(
    request_id: str,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    if str(user["id"]) not in _admin_ids():
        raise HTTPException(status_code=403, detail="Forbidden")
    from withdrawal_signer import approve_withdrawal
    try:
        return approve_withdrawal(
            request_id=request_id, admin_user_id=str(user["id"]), engine=get_engine()
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Withdrawal approval is not permitted")
    except ValueError:
        raise HTTPException(status_code=409, detail="Withdrawal cannot be approved")


@app.post("/api/admin/withdrawals/{request_id}/execute")
async def execute_admin_withdrawal(
    request_id: str,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    if str(user["id"]) not in _admin_ids():
        raise HTTPException(status_code=403, detail="Forbidden")
    from withdrawal_signer import execute_approved_withdrawal
    try:
        return await execute_approved_withdrawal(request_id=request_id, engine=get_engine())
    except PermissionError:
        raise HTTPException(status_code=403, detail="Withdrawal execution is disabled or not approved")
    except ValueError:
        raise HTTPException(status_code=409, detail="Withdrawal cannot be executed")
    except Exception as exc:
        logging.error("Withdrawal execution failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Withdrawal requires reconciliation")


@app.get("/api/inventory/mine")
async def get_my_native_inventory(x_telegram_init_data: str | None = Header(default=None)):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    db = get_engine()
    init_inventory_schema(db)
    with db.connect() as conn:
        rows = conn.execute(select(
            marketplace_assets.c.asset_id, marketplace_assets.c.asset_type,
            marketplace_assets.c.collection_address, marketplace_assets.c.token_id,
            marketplace_assets.c.custody_state, marketplace_assets.c.ownership_state,
            marketplace_listings.c.listing_id, marketplace_listings.c.price_raw,
            marketplace_listings.c.currency, marketplace_listings.c.status,
        ).select_from(
            marketplace_assets.outerjoin(
                marketplace_listings, marketplace_listings.c.asset_id == marketplace_assets.c.asset_id
            )
        ).where(marketplace_assets.c.owner_user_id == str(user["id"]))).mappings().all()
    return {"items": [dict(row) for row in rows]}


@app.post("/api/payments/stars/terms/accept")
async def accept_telegram_stars_terms(
    payload: StarsTermsAcceptanceRequest,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    database_url = os.getenv("DATABASE_URL", "").strip()
    terms_version = os.getenv("TELEGRAM_STARS_TERMS_VERSION", "").strip()
    terms_url = os.getenv("TERMS_URL", "").strip()
    if not (
        payload.accepted is True
        and os.getenv("GIFT_RADAR_STORAGE_BACKEND", "sqlite").strip().lower() == "postgres"
        and database_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://"))
        and terms_version
        and terms_url.startswith("https://")
    ):
        raise HTTPException(status_code=503, detail="Terms acceptance is not configured")
    try:
        result = record_telegram_stars_terms_acceptance(
            user_id=str(user["id"]),
            terms_version=terms_version,
            terms_url=terms_url,
        )
        return {
            "accepted": True,
            "terms_version": result["terms_version"],
            "accepted_at": result["accepted_at"],
            "duplicate": result["duplicate"],
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        logging.exception("Could not persist Telegram Stars terms acceptance")
        raise HTTPException(status_code=503, detail="Terms acceptance could not be stored")


@app.get("/api/payments/stars/policy")
async def telegram_stars_policy():
    enabled = _stars_payment_enabled()
    return {
        "enabled": enabled,
        "currency": "XTR",
        "provider": "Telegram Stars",
        "terms_url": os.getenv("TERMS_URL", "").strip() if enabled else None,
        "support_url": os.getenv("PAYMENT_SUPPORT_URL", "").strip() if enabled else None,
        "terms_version": os.getenv("TELEGRAM_STARS_TERMS_VERSION", "").strip() if enabled else None,
        "note": "Digital goods inside Telegram are payable only in Telegram Stars; explicit current terms acceptance is required before invoice creation.",
    }


@app.post("/api/marketplace/deals/{deal_id}/stars-invoice")
async def create_stars_invoice_endpoint(
    deal_id: str,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    if not _stars_payment_enabled():
        raise HTTPException(status_code=503, detail="Telegram Stars checkout is not enabled")
    try:
        invoice = create_telegram_stars_invoice(
            deal_id=deal_id,
            buyer_user_id=str(user["id"]),
        )
        from aiogram.types import LabeledPrice
        title = str(invoice.get("title") or "GIFT RADAR gift")[:32]
        description = ("GIFT RADAR order " + str(invoice["order_id"]))[:255]
        invoice_url = await _telegram_bot.create_invoice_link(
            title=title,
            description=description,
            payload=invoice["payload"],
            currency="XTR",
            prices=[LabeledPrice(label=title, amount=int(invoice["amount_stars"]))],
        )
        if not isinstance(invoice_url, str) or not invoice_url.startswith("https://"):
            raise RuntimeError("Telegram returned an invalid invoice link")
        return {
            "invoice_url": invoice_url,
            "invoice_id": invoice["invoice_id"],
            "amount": int(invoice["amount_stars"]),
            "currency": "XTR",
            "expires_at": int(invoice["expires_at"]),
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logging.warning("Telegram Stars invoice creation failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Telegram Stars invoice service is unavailable")


@app.post("/api/payments/stars/refunds")
async def request_stars_refund(
    payload: StarsRefundRequestBody,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    try:
        result = request_telegram_stars_refund(
            invoice_id=payload.invoice_id,
            buyer_user_id=str(user["id"]),
            reason=payload.reason,
        )
        return result
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logging.warning("Telegram Stars refund request failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Refund request could not be stored")


@app.post("/api/admin/payments/stars/refunds/{request_id}/execute")
async def execute_stars_refund(
    request_id: str,
    x_telegram_init_data: str | None = Header(default=None),
):
    admin = _require_admin(x_telegram_init_data)
    if _telegram_bot is None:
        raise HTTPException(status_code=503, detail="Telegram payment bot is unavailable")
    try:
        refund = prepare_telegram_stars_refund(request_id=request_id, admin_user_id=str(admin["id"]))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    try:
        confirmed = await _telegram_bot.refund_star_payment(
            user_id=int(refund["buyer_user_id"]),
            telegram_payment_charge_id=refund["telegram_payment_charge_id"],
        )
    except Exception as exc:
        # Telegram may have processed the refund before a timeout. Never retry automatically.
        logging.warning("Telegram Stars refund outcome is ambiguous (%s); manual reconciliation required", type(exc).__name__)
        try:
            require_telegram_stars_refund_reconciliation(
                request_id=request_id,
                reason="Telegram refund API outcome was ambiguous; verify Telegram transaction history before any further action",
            )
        except Exception as exc:
            logging.error("Stars refund reconciliation lock failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Refund outcome is ambiguous and locked for manual reconciliation; do not retry automatically")
    if not confirmed:
        fail_telegram_stars_refund(
            request_id=request_id,
            reason="Telegram explicitly rejected refundStarPayment",
        )
        raise HTTPException(status_code=409, detail="Telegram did not confirm the refund")
    try:
        result = complete_telegram_stars_refund(
            request_id=request_id,
            telegram_response_ref=refund["telegram_payment_charge_id"],
        )
        return result
    except Exception as exc:
        logging.error("Confirmed Stars refund ledger finalization failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Telegram confirmed refund; ledger reconciliation is required")


@app.get("/api/wallet/policy")
async def wallet_policy():
    """Public capability disclosure: TON Connect only; no signatures or payments."""
    return {
        "wallet_connect": "ton_connect_with_server_verified_ton_proof",
        "supported_network": "ton-mainnet",
        "stores": ["public_wallet_address_only_after_verified_wallet_proof"],
        "requests_seed_phrase": False,
        "requests_private_key": False,
        "signing_enabled": False,
        "transfers_enabled": False,
        "crypto_payments_enabled": False,
        "telegram_stars_payments_enabled": _stars_payment_enabled(),
        "payment_note": "Crypto checkout remains disabled pending platform/compliance approval.",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/status")
async def status():
    # Keep public status intentionally coarse; internal security posture and
    # infrastructure details belong in private operator monitoring.
    return {
        "status": "ok",
        "product": "GIFT RADAR",
        "mode": "independent_native_marketplace",
        "external_marketplaces": False,
        "marketplace_visibility": _marketplace_visibility(),
        "sources": {"external_marketplaces": "disabled_by_product_policy"},
    }


@app.get("/api/collections")
async def collections():
    return {"items": [], "connected": False}


@app.get("/api/gifts")
async def gifts(
    limit: int = Query(default=24, ge=1, le=40),
    offset: int = Query(default=0, ge=0, le=10000),
    search: str | None = Query(default=None, max_length=80),
    collection_id: str | None = Query(default=None, max_length=64),
):
    return {"items": [], "total_count": 0, "connected": False}


async def _refresh_public_catalogues():
    """No external marketplace polling. Native inventory is event-driven."""
    while True:
        await asyncio.sleep(_refresh_interval_seconds())


async def _native_nft_escrow_monitor_enabled() -> bool:
    """Fail closed until native NFT escrow has explicit legal and infrastructure approval."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    required = (
        "GIFT_RADAR_ESCROW_ADDRESS",
        "GIFT_RADAR_ESCROW_CODE_HASH",
        "TONAPI_KEY",
        "TONCENTER_API_KEY",
    )
    return bool(
        os.getenv("NATIVE_NFT_ESCROW_MONITOR_ENABLED", "false").strip().lower() == "true"
        and os.getenv("GIFT_RADAR_STORAGE_BACKEND", "").strip().lower() == "postgres"
        and database_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://"))
        and os.getenv("TON_NFT_PROVIDER_IDS", "").replace(" ", "").lower() == "tonapi,toncenter"
        and all(os.getenv(name, "").strip() for name in required)
        and os.getenv("KZ_LEGAL_REVIEW_APPROVED", "false").strip().lower() == "true"
        and os.getenv("KZ_DATA_RESIDENCY_VERIFIED", "false").strip().lower() == "true"
        and os.getenv("KZ_TAX_REVIEW_APPROVED", "false").strip().lower() == "true"
        and os.getenv("KZ_SECURITY_REVIEW_APPROVED", "false").strip().lower() == "true"
    )


async def _run_nft_inventory_monitor():
    """Poll pending seller deposits only after explicit escrow release gates pass."""
    while True:
        ready = _native_nft_escrow_monitor_enabled()
        if ready:
            try:
                result = await scan_pending_nft_listings(engine=get_engine())
                if result["activated"] or result["errors"]:
                    logging.info(
                        "NFT escrow monitor cycle completed; activated=%d; errors=%d",
                        int(result["activated"]), int(result["errors"]),
                    )
            except Exception as exc:
                logging.warning("NFT escrow monitor cycle failed (%s)", type(exc).__name__)
        await asyncio.sleep(20)


async def _run_marketplace_deal_expiry():
    """Release only unpaid, expired reservations; never touch paid or disputed deals."""
    import logging
    from marketplace_settlement import expire_unpaid_marketplace_deals

    while True:
        try:
            result = await asyncio.to_thread(
                expire_unpaid_marketplace_deals, limit=100,
            )
            if result["expired"]:
                logging.info(
                    "Marketplace unpaid deal expiry: expired=%s relisted=%s",
                    result["expired"], result["relisted"],
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.warning(
                "Marketplace deal expiry cycle failed (%s)", type(exc).__name__,
            )
        await asyncio.sleep(20)


@app.on_event("startup")
async def start_catalogue_refresh():
    global _refresh_task, _inventory_task, _deposit_task, _deal_expiry_task, _telegram_bot, _telegram_dispatcher, _telegram_webhook_secret
    init_marketplace_db()
    init_demo_offers_db()
    _refresh_task = asyncio.create_task(_refresh_public_catalogues())
    _inventory_task = asyncio.create_task(_run_nft_inventory_monitor())
    from ton_deposit_monitor import run_ton_deposit_monitor
    _deposit_task = asyncio.create_task(run_ton_deposit_monitor())
    _deal_expiry_task = asyncio.create_task(_run_marketplace_deal_expiry())

    token = os.getenv("BOT_TOKEN", "").strip()
    if token:
        try:
            from bot import create_bot, create_dispatcher
            _telegram_bot = create_bot()
            _telegram_dispatcher = create_dispatcher()
            _telegram_webhook_secret = os.getenv("BOT_WEBHOOK_SECRET", "").strip() or secrets.token_urlsafe(32)
            backend_url = os.getenv(
                "API_BASE_URL",
                "https://nft-gift-scanner-production.up.railway.app",
            ).rstrip("/")
            await _telegram_bot.set_webhook(
                url=f"{backend_url}/api/telegram/webhook",
                secret_token=_telegram_webhook_secret,
                allowed_updates=_telegram_dispatcher.resolve_used_update_types(),
            )
        except Exception as exc:
            import logging
            logging.warning("Telegram webhook configuration failed (%s)", type(exc).__name__)


@app.on_event("shutdown")
async def stop_catalogue_refresh():
    global _refresh_task, _inventory_task, _deposit_task, _deal_expiry_task, _telegram_bot
    if _refresh_task and not _refresh_task.done():
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass
    if _inventory_task and not _inventory_task.done():
        _inventory_task.cancel()
        try:
            await _inventory_task
        except asyncio.CancelledError:
            pass
    if _deal_expiry_task and not _deal_expiry_task.done():
        _deal_expiry_task.cancel()
        try:
            await _deal_expiry_task
        except asyncio.CancelledError:
            pass
    if _deposit_task and not _deposit_task.done():
        _deposit_task.cancel()
        try:
            await _deposit_task
        except asyncio.CancelledError:
            pass
    if _telegram_bot is not None:
        await _telegram_bot.session.close()
        _telegram_bot = None

