import os
import asyncio
import time
import json
import hmac
import hashlib
import secrets
import logging
from urllib.parse import parse_qsl
from pydantic import BaseModel
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Header, Request
from fastapi.middleware.cors import CORSMiddleware

from marketplace import create_marketplace_router, consume_telegram_init_data, create_telegram_session, get_telegram_session_user, get_app_setting, init_marketplace_db, set_app_setting, upsert_telegram_user, revoke_telegram_session, revoke_all_telegram_sessions, list_telegram_sessions, claim_telegram_update, finish_telegram_update, release_telegram_update
from demo_offers import create_demo_offers_router, init_demo_offers_db
from security_controls import SecurityControlsMiddleware, rate_limit_status
from financial_ledger import (
    create_telegram_stars_invoice, record_telegram_stars_terms_acceptance,
    request_telegram_stars_refund, prepare_telegram_stars_refund,
    complete_telegram_stars_refund, fail_telegram_stars_refund,
)

app = FastAPI(title="GIFT RADAR API", version="0.4.0")
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
    )


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
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    revoked = revoke_telegram_session(x_telegram_init_data or "")
    return {"authenticated": False, "revoked": revoked, "user_id": str(user["id"])}


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
        return {"verified": True, "address": result.get("address"), "network": result.get("network")}
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="TON Proof service is unreachable")



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
    except Exception:
        logging.exception("Could not create Telegram Stars invoice")
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
    except Exception:
        logging.exception("Could not create Telegram Stars refund request")
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
    except Exception:
        # Telegram may have processed the refund before a timeout. Never retry automatically.
        logging.exception("Telegram Stars refund outcome is ambiguous; manual reconciliation required")
        raise HTTPException(status_code=503, detail="Refund outcome is ambiguous; reconcile Telegram transactions before retrying")
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
    except Exception:
        logging.exception("Telegram confirmed refund but ledger finalization failed; reconciliation required")
        raise HTTPException(status_code=503, detail="Telegram confirmed refund; ledger reconciliation is required")


@app.get("/api/wallet/policy")
async def wallet_policy():
    """Public capability disclosure: TON Connect only; no signatures or payments."""
    return {
        "wallet_connect": "ton_connect_with_server_verified_ton_proof",
        "ton_proof_verification_configured": bool(os.getenv("TON_PROOF_SERVICE_URL", "").strip() and os.getenv("TON_PROOF_INTERNAL_TOKEN", "").strip()),
        "supported_network": "ton-mainnet",
        "stores": ["public_wallet_address_only_after_verified_wallet_proof"],
        "requests_seed_phrase": False,
        "requests_private_key": False,
        "signing_enabled": False,
        "transfers_enabled": False,
        "crypto_payments_enabled": False,
        "telegram_stars_payments_enabled": _stars_payment_enabled(),
        "payment_note": "Crypto checkout remains disabled pending platform/compliance approval.",
        "rate_limiting": rate_limit_status(),
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "nft-gift-scanner"}


@app.get("/api/status")
async def status():
    return {
        "mode": "independent_native_marketplace",
        "product": "GIFT RADAR",
        "sources": source_status(),
        "available_data": {"native_inventory": ["internally_created_listings", "verified_asset_records", "native_orders"]},
        "marketplace_visibility": _marketplace_visibility(),
        "external_marketplaces": False,
        "cross_market_comparison": False,
        "data_pipeline": {
            "delivery": "native_database_to_app_api",
            "external_marketplace_ingestion": False,
            "refresh_interval_seconds": _refresh_interval_seconds(),
        },
        "security": {
            "telegram_initdata_exchange": "signed_freshness_and_replay_protected",
            "api_sessions": "opaque_hashed_revocable_12h",
            "webhook": "secret_header_verified_idempotent_update_inbox",
            "user_wallet_access": False,
            "user_api_key_storage": False,
        },
        "note": "GIFT RADAR listings and orders are native platform records. External marketplace feeds are disabled. Native inventory indexing and verified listing publication remain release-gated.",
    }


@app.get("/api/collections")
async def collections():
    return {"items": [], "sources": source_status(), "updated_at": None, "connected": False,
            "note": "Native collection index is being built; external marketplace feeds are disabled."}


@app.get("/api/gifts")
async def gifts(
    limit: int = Query(default=24, ge=1, le=40),
    offset: int = Query(default=0, ge=0, le=10000),
    search: str | None = Query(default=None, max_length=80),
    collection_id: str | None = Query(default=None, max_length=64),
):
    return {"items": [], "total_count": 0, "connected": False, "sources": source_status(),
            "updated_at": None, "note": "Native GIFT RADAR inventory is not yet published."}


async def _refresh_public_catalogues():
    """No external marketplace polling. Native inventory is event-driven."""
    while True:
        await asyncio.sleep(_refresh_interval_seconds())


@app.on_event("startup")
async def start_catalogue_refresh():
    global _refresh_task, _telegram_bot, _telegram_dispatcher, _telegram_webhook_secret
    init_marketplace_db()
    init_demo_offers_db()
    _refresh_task = asyncio.create_task(_refresh_public_catalogues())

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
        except Exception:
            import logging
            logging.exception("Could not configure Telegram webhook")


@app.on_event("shutdown")
async def stop_catalogue_refresh():
    global _refresh_task, _telegram_bot
    if _refresh_task and not _refresh_task.done():
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass
    if _telegram_bot is not None:
        await _telegram_bot.session.close()
        _telegram_bot = None

