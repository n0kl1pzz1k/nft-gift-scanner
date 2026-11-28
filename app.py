import os
import asyncio
import time
import json
import hmac
import hashlib
import logging
import re
from pathlib import Path

import marketplace
from database import list_database_tables, list_database_columns
from db_schema_audit import build_runtime_inventory, build_postgres_runtime_inventory, build_postgres_migration_gate_summary
from urllib.parse import parse_qsl, urlsplit
from pydantic import BaseModel, Field

from fastapi import FastAPI, HTTPException, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from marketplace import create_marketplace_router, consume_telegram_init_data, create_telegram_session, get_telegram_session_user, init_marketplace_db, upsert_telegram_user, revoke_telegram_session, revoke_all_telegram_sessions, list_telegram_sessions, claim_telegram_update, finish_telegram_update, release_telegram_update, _enforce_user_rate_limit, sync_ton_nft_assets, sync_telegram_gift_assets, get_telegram_business_connection, get_telegram_business_connection_id, register_verified_asset, save_telegram_business_gift_ref, prune_telegram_business_gift_refs, get_telegram_business_gift_preview_ref, get_public_telegram_business_gift_preview_ref, save_verified_asset_traits
from ton_connect import init_ton_connect_db, create_proof_challenge, verify_ton_proof, consume_challenge_and_link_wallet, list_verified_wallets, revoke_verified_wallet
from ton_nft_inventory import (
    fetch_wallet_nfts,
    fragment_asset_type,
    fragment_collection_address,
    normalise_ton_address,
)
from ton_nft_chain import verify_nft_collection_membership, verify_nft_owner_onchain
from telegram_gift_inventory import fetch_user_telegram_gifts
from telegram_business_gift_inventory import fetch_business_account_gifts, fetch_sticker_preview
import httpx
from marketplace_offers import create_marketplace_offers_router, init_marketplace_offers_db, expire_unpaid_marketplace_deals, escalate_stalled_paid_deals
from marketplace_admin import create_marketplace_admin_router
from gram_deposits import (
    GramDepositError, build_ton_connect_comment_payload, confirm_gram_deposit,
    create_gram_deposit_intent, expire_gram_deposit_intents,
    initialize_gram_deposit_schema, list_pending_gram_deposits,
)
from gram_withdrawals import (
    GramWithdrawalError, create_gram_withdrawal, initialize_gram_withdrawal_schema,
    get_gram_withdrawal, list_gram_withdrawals, transition_gram_withdrawal,
)
from ton_multisig import fetch_ton_multisig_config, fetch_ton_multisig_orders, is_two_of_three_multisig

app = FastAPI(title="HASSLE NFT Market API", version="1.0.0")
app.include_router(create_marketplace_router(
    lambda value: _authenticate_telegram_credential(value),
    lambda value: _require_admin(value),
))
app.include_router(create_marketplace_offers_router(
    lambda value: _authenticate_telegram_credential(value),
    lambda value: _require_admin(value),
))
app.include_router(create_marketplace_admin_router(lambda value: _require_admin(value)))

_telegram_bot = None
_telegram_dispatcher = None
_telegram_webhook_secret = None
_shadow_migration_task = None
_deal_expiry_task = None
_gram_deposit_task = None




async def _run_postgres_shadow_migration_once():
    """Opt-in background shadow copy; production continues serving SQLite."""
    if os.getenv("HASSLE_RUN_POSTGRES_SHADOW_MIGRATION", "").strip() != "1":
        return
    run_id = os.getenv("HASSLE_POSTGRES_SHADOW_MIGRATION_ID", "").strip()
    dsn = os.getenv("HASSLE_POSTGRES_MIGRATION_DSN", "").strip()
    target_schema = os.getenv("HASSLE_POSTGRES_SHADOW_TARGET_SCHEMA", "").strip()
    if not run_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", run_id):
        logging.getLogger("hassle_nft.database").error("PostgreSQL shadow migration not started: invalid or missing run ID")
        return
    if not dsn:
        logging.getLogger("hassle_nft.database").error("PostgreSQL shadow migration not started: migration DSN is missing")
        return
    if not target_schema:
        target_schema = "hassle_shadow_" + run_id.replace("-", "_")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,48}", target_schema):
        logging.getLogger("hassle_nft.database").error("PostgreSQL shadow migration not started: invalid target schema")
        return
    try:
        batch_size = int(os.getenv("HASSLE_POSTGRES_SHADOW_BATCH_SIZE", "1000"))
    except ValueError:
        batch_size = 1000
    if not 1 <= batch_size <= 10000:
        logging.getLogger("hassle_nft.database").error("PostgreSQL shadow migration not started: invalid batch size")
        return

    source = Path(marketplace.DB_PATH).expanduser().resolve()
    backup_directory = os.getenv("HASSLE_POSTGRES_SHADOW_BACKUP_DIR", "").strip()
    backup_root = Path(backup_directory).expanduser() if backup_directory else source.parent
    backup = backup_root / f"hassle-postgres-shadow-{run_id}.sqlite3"
    marker = source.parent / f".hassle-postgres-shadow-{run_id}.json"
    try:
        from db_postgres_shadow_migration import run_shadow_migration_once
        result = await asyncio.to_thread(
            run_shadow_migration_once,
            str(source), str(backup), dsn, str(marker), run_id, target_schema, batch_size,
        )
        logging.getLogger("hassle_nft.database").warning(
            "postgresql_shadow_migration=%s",
            json.dumps(result, ensure_ascii=False, sort_keys=True),
        )
    except Exception as exc:
        logging.getLogger("hassle_nft.database").error(
            "PostgreSQL shadow migration task failed: %s", type(exc).__name__
        )


def _resolve_webhook_secret(bot_token: str) -> str:
    """Return a stable Telegram webhook secret across restarts and replicas.

    An explicit secret takes precedence. Otherwise derive a dedicated value
    from the bot token, so a process restart cannot invalidate Telegram's
    configured webhook header.
    """
    configured = os.getenv("BOT_WEBHOOK_SECRET", "").strip()
    if configured:
        if not (1 <= len(configured) <= 256) or not all(
            character in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in configured
        ):
            raise RuntimeError("BOT_WEBHOOK_SECRET must be 1-256 URL-safe characters")
        return configured
    token = str(bot_token or "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is required to derive a Telegram webhook secret")
    return hmac.new(
        token.encode("utf-8"),
        b"hassle-nft/telegram-webhook/v1",
        hashlib.sha256,
    ).hexdigest()

# Browser access is restricted to the production Mini App origins.
# Extra origins may be supplied explicitly for a controlled preview environment.
_CORS_DEFAULT_ORIGINS = [
    "https://hassle-nft-market.fj7kmzkhn9.workers.dev",
]


def _validate_cors_origins(raw_origins: str, *, app_env: str | None = None) -> list[str]:
    """Accept explicit HTTPS origins only; never permit wildcard or credential-bearing origins."""
    environment = (app_env or os.getenv("APP_ENV", "production")).strip().lower()
    allow_local_http = environment in {"test", "development"}
    origins: list[str] = []
    for raw_origin in str(raw_origins or "").split(","):
        origin = raw_origin.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlsplit(origin)
        try:
            parsed.port
        except ValueError as exc:
            raise RuntimeError("CORS_ALLOWED_ORIGINS contains an invalid port") from exc
        local_http = (
            allow_local_http
            and parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        if (
            origin == "*"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or (parsed.scheme != "https" and not local_http)
        ):
            raise RuntimeError("CORS_ALLOWED_ORIGINS must contain only explicit HTTPS origins")
        canonical = f"{parsed.scheme}://{parsed.netloc}"
        if canonical not in origins:
            origins.append(canonical)
    if not origins:
        raise RuntimeError("CORS_ALLOWED_ORIGINS must contain at least one valid origin")
    return origins


_CORS_ALLOWED_ORIGINS = _validate_cors_origins(
    os.getenv("CORS_ALLOWED_ORIGINS", ",".join(_CORS_DEFAULT_ORIGINS))
)

@app.middleware("http")
async def maintenance_mode_guard(request: Request, call_next):
    """Temporarily reject writes during the final SQLite snapshot and DB cutover."""
    if (
        os.getenv("HASSLE_MAINTENANCE_MODE", "").strip() == "1"
        and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    ):
        return JSONResponse(
            status_code=503,
            content={
                "detail": "HASSLE NFT is temporarily read-only for a database migration.",
                "maintenance": True,
            },
            headers={"Retry-After": "30", "Cache-Control": "no-store"},
        )
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Telegram-Init-Data"],
    max_age=600,
)

MAX_TELEGRAM_WEBHOOK_BODY_BYTES = 1_048_576


async def _read_limited_request_body(request: Request, max_bytes: int) -> bytes:
    """Read request chunks incrementally and reject oversized bodies before buffering them all."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid content length")
        if declared_length < 0:
            raise HTTPException(status_code=400, detail="Invalid content length")
        if declared_length > max_bytes:
            raise HTTPException(status_code=413, detail="Telegram update is too large")

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not isinstance(chunk, bytes):
            raise HTTPException(status_code=400, detail="Invalid request body")
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="Telegram update is too large")
        chunks.append(chunk)
    return b"".join(chunks)

class TelegramAuthRequest(BaseModel):
    init_data: str = Field(min_length=1, max_length=8192)


class TonProofDomain(BaseModel):
    lengthBytes: int = Field(ge=1, le=128)
    value: str = Field(min_length=1, max_length=128)


class TonProofData(BaseModel):
    timestamp: int = Field(ge=1)
    domain: TonProofDomain
    signature: str = Field(min_length=1, max_length=256)
    payload: str = Field(min_length=1, max_length=128)


class TonProofVerifyRequest(BaseModel):
    address: str = Field(min_length=1, max_length=128)
    network: str = Field(min_length=1, max_length=8)
    public_key: str | None = Field(default=None, min_length=64, max_length=64)
    proof: TonProofData
    state_init: str = Field(min_length=1, max_length=16384)


class GramDepositCreateRequest(BaseModel):
    amount_gram: str = Field(min_length=1, max_length=32)
    idempotency_key: str = Field(min_length=1, max_length=128)


class GramWithdrawalCreateRequest(BaseModel):
    amount_gram: str = Field(min_length=1, max_length=32)
    destination_address: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)


def _admin_ids() -> set[str]:
    raw = os.getenv("ADMIN_TELEGRAM_IDS", os.getenv("ADMIN_TELEGRAM_ID", ""))
    return {part.strip() for part in raw.split(",") if part.strip().isdigit()}


def _admin_roles() -> dict[str, str]:
    """Map allowlisted Telegram IDs to least-privilege admin roles."""
    raw = os.getenv("ADMIN_TELEGRAM_ROLES", "").strip()
    if not raw:
        return {user_id: "owner" for user_id in _admin_ids()}
    roles: dict[str, str] = {}
    allowed = {"owner", "moderator", "support", "finance"}
    for item in raw.split(","):
        user_id, separator, role = item.strip().partition(":")
        if separator and user_id.isdigit() and role in allowed and user_id in _admin_ids():
            roles[user_id] = role
    return roles


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
    user_id = str(user["id"])
    if user_id not in _admin_ids():
        raise HTTPException(status_code=403, detail="Administrator access only")
    role = _admin_roles().get(user_id)
    if role is None:
        raise HTTPException(status_code=403, detail="No administrator role is assigned")
    return {**user, "admin_role": role}


@app.post("/api/auth/telegram")
async def telegram_auth(payload: TelegramAuthRequest):
    user = _verify_telegram_init_data(payload.init_data)
    _enforce_user_rate_limit(str(user["id"]), "telegram_auth_exchange", 30, 60)
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

@app.get("/api/marketplace/balance/me")
async def marketplace_balance_me(x_telegram_init_data: str | None = Header(default=None)):
    """Return spendable user funds separately from unsettled seller proceeds."""
    from decimal import Decimal

    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    account_id = str(user["id"])
    try:
        with marketplace._db() as connection:
            rows = connection.execute(
                """SELECT account_type, direction, amount
                   FROM marketplace_ledger_entries
                   WHERE account_type IN ('user_balance', 'seller_payable')
                     AND account_id = ? AND currency = 'TON'""",
                (account_id,),
            ).fetchall()
        balances = {"user_balance": Decimal("0"), "seller_payable": Decimal("0")}
        for row in rows:
            amount = Decimal(str(row["amount"]))
            account_type = str(row["account_type"])
            if account_type not in balances or amount <= 0:
                raise ValueError("Unexpected marketplace ledger account or amount")
            balances[account_type] += amount if row["direction"] == "credit" else -amount
        if any(value < 0 for value in balances.values()):
            logging.getLogger("hassle_nft.ledger").error(
                "Negative account balance detected for Telegram user %s", account_id
            )
            raise HTTPException(status_code=503, detail="Ledger balance requires reconciliation")
        return {
            "currency": "TON",
            "display_currency": "GRAM",
            "balance": format(balances["user_balance"], "f"),
            "seller_pending_balance": format(balances["seller_payable"], "f"),
            "seller_pending_status": "pending_manual_payout" if balances["seller_payable"] else "none",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logging.getLogger("hassle_nft.ledger").exception("Unable to read marketplace ledger balances")
        raise HTTPException(status_code=503, detail="Ledger balance is temporarily unavailable") from exc

def _gram_deposit_configuration() -> tuple[str, str]:
    if os.getenv("HASSLE_TON_CHECKOUT_ENABLED", "").strip() != "1":
        raise HTTPException(status_code=503, detail="GRAM deposits are temporarily unavailable")
    treasury = os.getenv("HASSLE_TON_TREASURY_ADDRESS", "").strip()
    network = os.getenv("HASSLE_TON_NETWORK", "mainnet").strip().lower()
    if not treasury or network not in {"mainnet", "testnet"}:
        raise HTTPException(status_code=503, detail="GRAM deposit treasury is not configured")
    return treasury, network


@app.post("/api/payments/gram/deposits")
async def create_gram_deposit(
    payload: GramDepositCreateRequest,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    user_id = str(user["id"])
    _enforce_user_rate_limit(user_id, "gram_deposit_create", 8, 3600)
    treasury, network = _gram_deposit_configuration()
    ttl = max(300, min(int(os.getenv("HASSLE_GRAM_DEPOSIT_TTL_SECONDS", "1800")), 86400))
    try:
        with marketplace._db() as connection:
            marketplace.begin_write(connection, f"gram-deposit-intent:{user_id}:{payload.idempotency_key}")
            try:
                intent = create_gram_deposit_intent(
                    connection, user_id=user_id, treasury_address=treasury,
                    amount_gram=payload.amount_gram, network=network,
                    idempotency_key=payload.idempotency_key, ttl_seconds=ttl,
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        ton_connect_payload = build_ton_connect_comment_payload(intent["memo"])
        return {
            "id": intent["id"],
            "status": intent["status"],
            "currency": "GRAM",
            "amount_nano": str(intent["amount_nano"]),
            "address": intent["treasury_address"],
            "memo": intent["memo"],
            "network": intent["network"],
            "expires_at": intent["expires_at"],
            "ton_connect": {
                "validUntil": intent["expires_at"],
                "network": "-239" if network == "mainnet" else "-3",
                "messages": [{
                    "address": intent["treasury_address"],
                    "amount": str(intent["amount_nano"]),
                    "payload": ton_connect_payload,
                }],
            },
        }
    except GramDepositError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logging.getLogger("hassle_nft.gram").exception("Could not create GRAM deposit intent")
        raise HTTPException(status_code=503, detail="Could not create GRAM deposit request") from exc


@app.get("/api/payments/gram/deposits/{deposit_id}")
async def get_gram_deposit(
    deposit_id: str,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    with marketplace._db() as connection:
        row = connection.execute(
            """SELECT id,status,amount_nano,network,created_at,expires_at,
                      confirmed_at,chain_tx_hash
               FROM gram_deposit_intents WHERE id=? AND user_id=?""",
            (deposit_id, str(user["id"])),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="GRAM deposit not found")
    return {
        "id": row["id"], "status": row["status"], "currency": "GRAM",
        "amount_nano": str(row["amount_nano"]), "network": row["network"],
        "created_at": row["created_at"], "expires_at": row["expires_at"],
        "confirmed_at": row["confirmed_at"], "transaction_hash": row["chain_tx_hash"],
    }


@app.get("/api/payments/gram/deposits/active")
async def get_active_gram_deposit(x_telegram_init_data: str | None = Header(default=None)):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    treasury, network = _gram_deposit_configuration()
    with marketplace._db() as connection:
        row = connection.execute(
            """SELECT * FROM gram_deposit_intents
               WHERE user_id=? AND network=? AND status='pending' AND expires_at>?
               ORDER BY created_at DESC LIMIT 1""",
            (str(user["id"]), network, int(time.time())),
        ).fetchone()
    if row is None:
        return {"deposit": None}
    intent = dict(row)
    return {
        "deposit": {
            "id": intent["id"],
            "amount_nano": str(intent["amount_nano"]),
            "network": network,
            "ton_connect": {
                "validUntil": intent["expires_at"],
                "network": "-239" if network == "mainnet" else "-3",
                "messages": [{
                    "address": treasury,
                    "amount": str(intent["amount_nano"]),
                    "payload": build_ton_connect_comment_payload(intent["memo"]),
                }],
            },
        }
    }


@app.post("/api/payments/gram/withdrawals")
async def create_gram_withdrawal_request(
    payload: GramWithdrawalCreateRequest,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    user_id = str(user["id"])
    _enforce_user_rate_limit(user_id, "gram_withdrawal_create", 3, 86400)
    if os.getenv("HASSLE_TON_WITHDRAWAL_REQUESTS_ENABLED", "").strip() != "1":
        raise HTTPException(status_code=503, detail="GRAM withdrawals are not enabled yet")
    treasury = os.getenv("HASSLE_TON_TREASURY_ADDRESS", "").strip()
    network = os.getenv("HASSLE_TON_NETWORK", "mainnet").strip().lower()
    if not treasury or network not in {"mainnet", "testnet"}:
        raise HTTPException(status_code=503, detail="GRAM treasury is not configured")
    try:
        fee_bps = int(os.getenv("HASSLE_GRAM_WITHDRAWAL_FEE_BPS", "0"))
        min_amount = int(os.getenv("HASSLE_GRAM_WITHDRAWAL_MIN_NANO", "100000000"))
        max_amount = int(os.getenv("HASSLE_GRAM_WITHDRAWAL_MAX_NANO", "100000000000000"))
        with marketplace._db() as connection:
            withdrawal = create_gram_withdrawal(
                connection, user_id=user_id,
                destination_address=payload.destination_address,
                amount_gram=payload.amount_gram, network=network,
                idempotency_key=payload.idempotency_key, fee_bps=fee_bps,
                min_amount_nano=min_amount, max_amount_nano=max_amount,
            )
        return {
            "id": withdrawal["id"], "status": withdrawal["status"],
            "currency": "GRAM", "amount_nano": str(withdrawal["amount_nano"]),
            "fee_nano": str(withdrawal["fee_nano"]),
            "payout_nano": str(withdrawal["payout_nano"]),
            "destination_address": withdrawal["destination_address"],
            "network": withdrawal["network"],
            "automatic_payout_available": False,
            "message": "Withdrawal reserved; multisig execution is not enabled.",
        }
    except GramWithdrawalError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logging.getLogger("hassle_nft.gram").exception("Could not create GRAM withdrawal request")
        raise HTTPException(status_code=503, detail="Could not create GRAM withdrawal request") from exc


@app.post("/api/payments/gram/withdrawals/{withdrawal_id}/cancel")
async def cancel_my_gram_withdrawal(
    withdrawal_id: str,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    with marketplace._db() as connection:
        try:
            withdrawal = get_gram_withdrawal(connection, withdrawal_id, str(user["id"]))
            if withdrawal["status"] != "queued":
                raise HTTPException(status_code=409, detail="Only queued GRAM withdrawals can be cancelled")
            result = transition_gram_withdrawal(
                connection, withdrawal_id=withdrawal_id, new_status="cancelled",
                event_key=f"{withdrawal_id}:user-cancel:{user['id']}",
            )
            return {"id": result["id"], "status": result["status"], "currency": "GRAM"}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=404, detail="GRAM withdrawal not found or cannot be cancelled") from exc


@app.get("/api/payments/gram/withdrawals")
async def list_my_gram_withdrawals(
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    with marketplace._db() as connection:
        rows = list_gram_withdrawals(connection, str(user["id"]))
    return {
        "currency": "GRAM",
        "items": [{
            "id": row["id"], "status": row["status"],
            "amount_nano": str(row["amount_nano"]),
            "fee_nano": str(row["fee_nano"]),
            "payout_nano": str(row["payout_nano"]),
            "destination_address": row["destination_address"],
            "network": row["network"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "chain_tx_hash": row["chain_tx_hash"],
        } for row in rows],
    }


@app.get("/api/admin/payments/gram/multisig/status")
async def gram_multisig_production_status(
    x_telegram_init_data: str | None = Header(default=None),
):
    """Read-only production verification; never creates or signs a multisig order."""
    admin = _require_admin(x_telegram_init_data or "")
    if admin.get("admin_role") not in {"owner", "finance"}:
        raise HTTPException(status_code=403, detail="Finance administrator access only")
    admin_id = str(admin["id"])
    _enforce_user_rate_limit(admin_id, "gram_multisig_status", 12, 60)

    treasury = os.getenv("HASSLE_TON_TREASURY_ADDRESS", "").strip()
    expected = os.getenv("HASSLE_TON_MULTISIG_EXPECTED_ADDRESS", "").strip()
    network = os.getenv("HASSLE_TON_NETWORK", "mainnet").strip().lower()
    if not treasury or not expected:
        return {
            "configured": False,
            "verified": False,
            "network": network,
            "payout_execution_enabled": False,
            "missing": [
                name for name, value in (
                    ("HASSLE_TON_TREASURY_ADDRESS", treasury),
                    ("HASSLE_TON_MULTISIG_EXPECTED_ADDRESS", expected),
                    ("HASSLE_TON_MULTISIG_CODE_HASH", os.getenv("HASSLE_TON_MULTISIG_CODE_HASH", "").strip()),
                    ("HASSLE_TON_MULTISIG_DATA_HASH", os.getenv("HASSLE_TON_MULTISIG_DATA_HASH", "").strip()),
                    ("TONAPI_TOKEN", os.getenv("TONAPI_TOKEN", "").strip()),
                ) if not value
            ],
        }
    if network != "mainnet":
        return {
            "configured": True,
            "verified": False,
            "network": network,
            "payout_execution_enabled": False,
            "error": "Production multisig verification currently requires mainnet configuration.",
        }
    if not os.getenv("TONAPI_TOKEN", "").strip():
        return {
            "configured": True,
            "verified": False,
            "network": network,
            "payout_execution_enabled": False,
            "missing": ["TONAPI_TOKEN"],
        }

    try:
        config = await fetch_ton_multisig_config(treasury)
        verified = is_two_of_three_multisig(config, treasury)
        orders = await fetch_ton_multisig_orders(
            treasury, int(config["next_order_seqno"]), limit=20
        ) if verified else []
    except Exception as exc:
        logging.getLogger("hassle_nft.gram").warning(
            "Production multisig verification failed: %s", type(exc).__name__
        )
        raise HTTPException(
            status_code=503,
            detail="Could not independently verify the GRAM treasury on-chain.",
            headers={"Retry-After": "30"},
        ) from exc

    return {
        "configured": True,
        "verified": verified,
        "network": network,
        "address": config.get("address"),
        "threshold": config.get("threshold"),
        "signers": config.get("signers", []),
        "proposers": config.get("proposers", []),
        "next_order_seqno": config.get("next_order_seqno"),
        "code_hash_matches": bool(
            config.get("code_hash")
            and config.get("code_hash") == os.getenv("HASSLE_TON_MULTISIG_CODE_HASH", "").strip().lower()
        ),
        "data_hash_matches": bool(
            config.get("data_hash")
            and config.get("data_hash") == os.getenv("HASSLE_TON_MULTISIG_DATA_HASH", "").strip().lower()
        ),
        "active_orders": [{
            "address": order.get("address"),
            "order_seqno": order.get("order_seqno"),
            "threshold": order.get("threshold"),
            "approvals_num": order.get("approvals_num"),
            "approvals": order.get("approvals", []),
            "expiration_date": order.get("expiration_date"),
            "sent_for_execution": order.get("sent_for_execution"),
            "actions": order.get("actions", []),
            "approval_safe": order.get("approval_safe", False),
        } for order in orders],
        "payout_execution_enabled": False,
        "next_step": (
            "Treasury verification passed. Multisig order proposal and external signer approval flow are still required."
            if verified else
            "Treasury does not match the pinned 2-of-3 production configuration."
        ),
    }


async def _run_gram_deposit_indexer():
    """Poll canonical treasury account history; client callbacks never credit funds."""
    while True:
        try:
            if (
                os.getenv("HASSLE_TON_CHECKOUT_ENABLED", "").strip() == "1"
                and os.getenv("TONAPI_TOKEN", "").strip()
            ):
                treasury, network = _gram_deposit_configuration()
                base_url = (
                    "https://tonapi.io" if network == "mainnet"
                    else "https://testnet.tonapi.io"
                )
                token = os.getenv("TONAPI_TOKEN", "").strip()
                with marketplace._db() as connection:
                    pending = list_pending_gram_deposits(connection, network=network)
                if pending:
                    oldest_created = min(int(item["created_at"]) for item in pending)
                    before_lt = None
                    processed = 0
                    async with httpx.AsyncClient(
                        timeout=20,
                        headers={"Authorization": f"Bearer {token}"},
                    ) as client:
                        for _page in range(20):
                            params = {"limit": 100}
                            if before_lt:
                                params["before_lt"] = before_lt
                            response = await client.get(
                                f"{base_url}/v2/accounts/{treasury}/transactions",
                                params=params,
                            )
                            response.raise_for_status()
                            body = response.json()
                            transactions = body if isinstance(body, list) else body.get("transactions", [])
                            if not transactions:
                                break
                            oldest_lt = None
                            oldest_utime = None
                            for transaction in transactions:
                                try:
                                    tx_lt = int(transaction.get("lt") or 0)
                                    tx_utime = int(transaction.get("utime") or 0)
                                    oldest_lt = tx_lt if oldest_lt is None else min(oldest_lt, tx_lt)
                                    oldest_utime = tx_utime if oldest_utime is None else min(oldest_utime, tx_utime)
                                    with marketplace._db() as connection:
                                        result = confirm_gram_deposit(
                                            connection, network=network,
                                            transaction=transaction,
                                        )
                                    if result:
                                        processed += 1
                                except Exception:
                                    logging.getLogger("hassle_nft.gram").exception(
                                        "GRAM transaction verification failed"
                                    )
                            if oldest_utime is not None and oldest_utime < oldest_created:
                                break
                            if oldest_lt is None:
                                break
                            before_lt = str(oldest_lt)
                    if processed:
                        logging.getLogger("hassle_nft.gram").info(
                            "confirmed_gram_deposits=%s", processed
                        )
                with marketplace._db() as connection:
                    expire_gram_deposit_intents(connection)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.getLogger("hassle_nft.gram").warning(
                "GRAM deposit indexer cycle failed: %s", type(exc).__name__
            )
        await asyncio.sleep(15)


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

    raw_body = await _read_limited_request_body(request, MAX_TELEGRAM_WEBHOOK_BODY_BYTES)
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
    _enforce_user_rate_limit(str(user["id"]), "ton_proof_payload", 10, 60)
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
    _enforce_user_rate_limit(str(user["id"]), "ton_proof_verify", 10, 60)
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


@app.delete("/api/ton-proof/wallets/{network}/{address}")
async def ton_proof_wallet_revoke(
    network: str,
    address: str,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    owner_id = str(user["id"])
    _enforce_user_rate_limit(owner_id, "revoke_ton_wallet", 10)
    try:
        revoked = revoke_verified_wallet(owner_id, network, address)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not revoked:
        # Deliberately do not reveal whether the wallet belongs to another account.
        raise HTTPException(status_code=404, detail="Verified wallet not found")
    return {"revoked": True}


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

    # Every imported TON NFT must pass a live get_nft_data() owner check.
    # Fragment items additionally pass the collection contract's address-by-index
    # check so an indexer-supplied collection label cannot spoof a collectible.
    linked_wallet_addresses = {str(wallet.get("address") or "") for wallet in wallets}
    try:
        for item in all_items:
            external_id = str(item.get("address") or "").strip()
            evidence_wallet = str(item.get("wallet_address") or "").strip()
            if not external_id or evidence_wallet not in linked_wallet_addresses:
                raise HTTPException(
                    status_code=409,
                    detail="TON NFT could not be bound to one of the verified wallets; no ownership records were changed",
                )

            # The live item contract, not the indexer's collection label, decides
            # whether this is a Fragment username or anonymous number.
            live_proof = await verify_nft_owner_onchain(external_id, evidence_wallet)
            if live_proof.get("verified") is not True:
                raise HTTPException(
                    status_code=409,
                    detail="Live TON NFT owner did not match the verified wallet; no ownership records were changed",
                )
            live_collection = str(live_proof.get("collection_address") or "").strip()
            indexed_collection = str(item.get("collection_address") or "").strip()
            if indexed_collection:
                try:
                    collections_match = (
                        bool(live_collection)
                        and normalise_ton_address(indexed_collection)
                        == normalise_ton_address(live_collection)
                    )
                except Exception:
                    collections_match = False
                if not collections_match:
                    raise HTTPException(
                        status_code=409,
                        detail="TON NFT indexer collection did not match the live contract; no ownership records were changed",
                    )

            asset_type = fragment_asset_type(live_collection) if live_collection else None
            if asset_type:
                trusted_collection = fragment_collection_address(asset_type)
                if not trusted_collection:
                    raise HTTPException(
                        status_code=409,
                        detail="Fragment collection is not in the verified collection allowlist",
                    )
                collection_proof = await verify_nft_collection_membership(
                    external_id,
                    trusted_collection,
                    evidence_wallet,
                )
                block_seqno = collection_proof.get("block_seqno")
                if (
                    collection_proof.get("verified") is not True
                    or isinstance(block_seqno, bool)
                    or not isinstance(block_seqno, int)
                    or block_seqno < 0
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="Fragment collection membership or current owner did not match; no ownership records were changed",
                    )
                item["asset_type"] = asset_type
                item["collection_address"] = trusted_collection
                item["verification_reference"] = (
                    f"ton-contract-v1:{asset_type}:{block_seqno}:"
                    f"{trusted_collection}:{external_id}:{evidence_wallet}"
                )
            else:
                item["collection_address"] = live_collection
                item["verification_reference"] = (
                    f"ton-contract-owner-v1:{live_proof['provider']}:"
                    f"{live_proof['owner_address']}:{live_collection or 'none'}:{live_proof.get('index')}"
                )
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=503,
            detail="TON contract verification is temporarily unavailable; no ownership records were changed",
            headers={"Retry-After": "30"},
        ) from exc

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


@app.get("/api/marketplace/assets/{asset_id}/preview")
async def get_verified_gift_preview(asset_id: str, x_telegram_init_data: str | None = Header(default=None)):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    owner_id = str(user["id"])
    _enforce_user_rate_limit(owner_id, "gift_preview", 30)
    try:
        preview_ref = get_telegram_business_gift_preview_ref(asset_id, owner_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid asset owner") from exc
    if not preview_ref:
        raise HTTPException(status_code=404, detail="Verified gift preview not found")
    file_id = str(preview_ref.get("model_sticker_file_id") or "")
    if not file_id:
        raise HTTPException(status_code=404, detail="Telegram has no static preview for this gift model")
    try:
        image_data, media_type = await fetch_sticker_preview(file_id)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Telegram gift preview is temporarily unavailable", headers={"Retry-After": "30"}) from exc
    return Response(
        content=image_data,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@app.get("/api/marketplace/public-assets/{asset_id}/preview")
async def get_public_verified_gift_preview(asset_id: str, request: Request):
    # Public thumbnails are only available for currently active listings.
    # Rate-limit by source IP and never expose Telegram file IDs to clients.
    client_ip = request.client.host if request.client else "unknown"
    _enforce_user_rate_limit(f"public:{client_ip}", "public_gift_preview", 60, 60)
    preview_ref = get_public_telegram_business_gift_preview_ref(asset_id)
    if not preview_ref:
        raise HTTPException(status_code=404, detail="Public gift preview not found")
    file_id = str(preview_ref.get("model_sticker_file_id") or "")
    if not file_id:
        raise HTTPException(status_code=404, detail="Telegram has no static preview for this gift model")
    try:
        image_data, media_type = await fetch_sticker_preview(file_id)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Telegram gift preview is temporarily unavailable", headers={"Retry-After": "30"}) from exc
    return Response(
        content=image_data,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@app.post("/api/marketplace/assets/import-telegram")
async def import_telegram_gifts(x_telegram_init_data: str | None = Header(default=None)):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    telegram_id = str(user["id"])
    _enforce_user_rate_limit(telegram_id, "import_telegram_gifts", 3, 60)

    # Prefer Telegram Business whenever the owner has granted gift-view access.
    # This keeps the primary inventory import bound to owned_gift_id and stores
    # the model sticker handle needed by the protected preview endpoint, even
    # when a cached Mini App client calls the legacy import-telegram route.
    if get_telegram_business_connection_id(telegram_id):
        return await import_business_account_gifts(x_telegram_init_data)

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


@app.post("/api/marketplace/assets/import-business-gifts")
async def import_business_account_gifts(x_telegram_init_data: str | None = Header(default=None)):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    owner_id = str(user["id"])
    _enforce_user_rate_limit(owner_id, "import_business_gifts", 4)
    connection_id = get_telegram_business_connection_id(owner_id)
    if not connection_id:
        raise HTTPException(status_code=409, detail="Connect Telegram Business and grant permission to view gifts first")
    try:
        snapshot = await fetch_business_account_gifts(connection_id)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Telegram Business gift inventory is temporarily unavailable") from exc
    registered = []
    active_owned_ids = set()
    active_gift_slugs = set()
    try:
        for item in snapshot["items"]:
            owned_id = str(item["owned_gift_id"])
            active_owned_ids.add(owned_id)
            active_gift_slugs.add(str(item["external_asset_id"]))
            asset = register_verified_asset(
                source="telegram_gift",
                external_asset_id=str(item["external_asset_id"]),
                owner_telegram_id=owner_id,
                title=str(item["title"]),
                asset_type="gift",
                image_url="",
                verification_method="telegram_gift_owner",
                verification_reference=(
                    f"telegram-business-api:getBusinessAccountGifts:{owner_id}:"
                    f"{connection_id}:{owned_id}:{item.get('send_date', '')}:{time.time()}"
                ),
                allow_owner_transfer=True,
            )
            save_verified_asset_traits(asset["id"], item, source="telegram-business")
            save_telegram_business_gift_ref(
                asset_id=asset["id"],
                owner_telegram_id=owner_id,
                business_connection_id=connection_id,
                owned_gift_id=owned_id,
                gift_slug=str(item["external_asset_id"]),
                model_sticker_file_id=str(item.get("model_sticker_file_id") or ""),
            )
            registered.append(asset)
        if snapshot["complete"]:
            prune_telegram_business_gift_refs(
                owner_telegram_id=owner_id,
                business_connection_id=connection_id,
                active_owned_gift_ids=active_owned_ids,
                active_gift_slugs=active_gift_slugs,
            )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="Telegram Business gift ownership could not be safely reconciled") from exc
    return {
        "imported": len(registered),
        "items": registered,
        "ownership_scan_complete": snapshot["complete"],
        "telegram_gifts_seen": snapshot["total_count"],
        "supported_unique_gifts": snapshot["supported_unique_count"],
    }


@app.get("/api/telegram-business/status")
async def telegram_business_status(x_telegram_init_data: str | None = Header(default=None)):
    user = _authenticate_telegram_credential(x_telegram_init_data or "")
    connection = get_telegram_business_connection(str(user["id"]))
    if connection is None:
        return {
            "connected": False,
            "can_view_gifts_and_stars": False,
            "can_transfer_and_upgrade_gifts": False,
            "can_transfer_stars": False,
            "gift_import_ready": False,
            "gift_transfer_ready": False,
        }
    return connection


@app.get("/health")
async def health():
    """Liveness probe: confirms that the API process is responding."""
    return {"status": "ok", "service": "hassle-nft-market"}


_READINESS_TABLES = (
    # Identity, sessions, Telegram update processing, and user data.
    "telegram_users",
    "user_profiles",
    "telegram_sessions",
    "telegram_initdata_uses",
    "telegram_update_inbox",
    "user_favorites",
    # Verified assets and marketplace listings.
    "marketplace_orders",
    "verified_assets",
    "marketplace_order_events",
    "marketplace_order_revisions",
    "marketplace_order_reports",
    "asset_ownership_history",
    # Offers, deals, lifecycle, and notifications.
    "marketplace_offers",
    "marketplace_deals",
    "marketplace_deal_events",
    "marketplace_deal_lifecycle_events",
    "marketplace_notifications",
    # Application controls and Telegram Business ownership links.
    "api_rate_limits",
    "security_audit_events",
    "app_settings",
    "telegram_business_connections",
    "telegram_business_gift_refs",
    # TON Connect proof and verified wallet bindings.
    "ton_proof_challenges",
    "verified_wallets",
    # Payment intents, provider evidence, immutable ledger and escrow approvals.
    "marketplace_payment_intents",
    "marketplace_payment_events",
    "marketplace_ledger_transactions",
    "marketplace_ledger_entries",
    "marketplace_escrow_events",
    "marketplace_dispute_resolution_approvals",
)

# Required columns are checked separately from table presence so an incomplete
# or unexpectedly old schema cannot pass readiness and receive production traffic.
_READINESS_COLUMNS = {
    "telegram_users": {"telegram_id", "updated_at"},
    "telegram_sessions": {"token_hash", "telegram_id", "expires_at"},
    "telegram_update_inbox": {"update_id", "payload_hash", "status", "lease_until"},
    "marketplace_orders": {"id", "user_id", "status", "asset_type", "asset_id"},
    "verified_assets": {"id", "source", "external_asset_id", "owner_telegram_id", "status"},
    "marketplace_offers": {"id", "order_id", "sender_user_id", "amount", "currency", "status"},
    "marketplace_deals": {"id", "order_id", "offer_id", "status"},
    "marketplace_deal_events": {"id", "deal_id", "event_type"},
    "marketplace_deal_lifecycle_events": {"id", "deal_id", "event_type"},
    "marketplace_notifications": {"id", "user_id", "offer_id", "order_id", "read_at"},
    "telegram_business_connections": {"telegram_id", "business_connection_id", "is_enabled"},
    "telegram_business_gift_refs": {"asset_id", "owner_telegram_id", "owned_gift_id"},
    "ton_proof_challenges": {"payload_hash", "telegram_id", "session_hash", "expires_at"},
    "verified_wallets": {"network", "address", "telegram_id", "state_init_hash", "revoked_at"},
    "security_audit_events": {"id", "actor_telegram_id", "action", "target_type", "target_hash", "outcome", "created_at"},
    "marketplace_payment_intents": {"id", "deal_id", "provider", "currency", "amount", "status", "idempotency_key", "expires_at", "created_at", "updated_at"},
    "marketplace_payment_events": {"id", "provider", "provider_event_id", "payment_intent_id", "event_type", "payload_hash", "created_at"},
    "marketplace_ledger_transactions": {"id", "idempotency_key", "reference_type", "reference_id", "currency", "amount", "created_at"},
    "marketplace_ledger_entries": {"id", "transaction_id", "account_type", "account_id", "direction", "currency", "amount", "created_at"},
    "marketplace_escrow_events": {"id", "deal_id", "actor_user_id", "event_type", "event_key", "event_data", "created_at"},
    "marketplace_dispute_resolution_approvals": {"id", "deal_id", "admin_user_id", "decision", "idempotency_key", "created_at"},
}


@app.get("/ready")
async def readiness():
    """Readiness probe: verify database connectivity and critical marketplace schema."""
    try:
        with marketplace._db() as connection:
            connection.execute("SELECT 1").fetchone()
            existing = list_database_tables(connection)
            missing = [table for table in _READINESS_TABLES if table not in existing]
            missing_columns = []
            for table, required_columns in _READINESS_COLUMNS.items():
                if table not in existing:
                    continue
                actual_columns = list_database_columns(connection, table)
                missing_columns.extend(
                    f"{table}.{column}"
                    for column in sorted(required_columns - actual_columns)
                )
            if missing or missing_columns:
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "not_ready",
                        "service": "hassle-nft-market",
                        "dependency": "database_schema",
                        "missing_tables": missing,
                        "missing_columns": missing_columns,
                    },
                )
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "service": "hassle-nft-market", "dependency": "database"},
        )
    return {
        "status": "ready",
        "service": "hassle-nft-market",
        "dependencies": {"database": "ok", "database_schema": "ok"},
    }


async def _run_marketplace_expiry_sweeper():
    """Expire stale unpaid reservations; every mutation is serialized in the database."""
    while True:
        try:
            result = await asyncio.to_thread(expire_unpaid_marketplace_deals)
            delivery_result = await asyncio.to_thread(escalate_stalled_paid_deals)
            result.update(delivery_result)
            if result.get("expired_deals") or result.get("stalled_deliveries_disputed"):
                logging.getLogger("hassle_nft.escrow").info(
                    "reservation_timeout_sweep=%s",
                    json.dumps(result, sort_keys=True),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger("hassle_nft.escrow").exception("Reservation timeout sweep failed")
        await asyncio.sleep(60)


@app.on_event("startup")
async def start_services():
    global _telegram_bot, _telegram_dispatcher, _telegram_webhook_secret, _deal_expiry_task, _gram_deposit_task
    init_marketplace_db()
    init_marketplace_offers_db()
    init_ton_connect_db()
    with marketplace._db() as connection:
        initialize_gram_deposit_schema(connection)
        initialize_gram_withdrawal_schema(connection)
    _deal_expiry_task = asyncio.create_task(_run_marketplace_expiry_sweeper())
    _gram_deposit_task = asyncio.create_task(_run_gram_deposit_indexer())
    # Emit a privacy-safe inventory of the actual mounted SQLite database.
    # This records schema metadata and row counts only, never row contents.
    with marketplace._db() as connection:
        if bool(getattr(connection, "supports_for_update", False)):
            inventory = build_postgres_runtime_inventory(connection)
            inventory_event = "postgresql_runtime_inventory"
        else:
            inventory = build_runtime_inventory(connection)
            inventory_event = "sqlite_runtime_inventory"
    logging.getLogger("hassle_nft.database").warning(
        "%s=%s",
        inventory_event,
        json.dumps(inventory, ensure_ascii=False, sort_keys=True),
    )
    logging.getLogger("hassle_nft.database").warning(
        "postgresql_migration_gate_summary=%s",
        json.dumps(build_postgres_migration_gate_summary(inventory), ensure_ascii=False, sort_keys=True),
    )
    global _shadow_migration_task
    if os.getenv("HASSLE_RUN_POSTGRES_SHADOW_MIGRATION", "").strip() == "1":
        _shadow_migration_task = asyncio.create_task(_run_postgres_shadow_migration_once())

    token = os.getenv("BOT_TOKEN", "").strip()
    if token:
        try:
            from bot import create_bot, create_dispatcher
            _telegram_bot = create_bot()
            _telegram_dispatcher = create_dispatcher()
            _telegram_webhook_secret = _resolve_webhook_secret(token)
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
            logging.exception("Could not configure Telegram webhook")


@app.on_event("shutdown")
async def stop_services():
    global _telegram_bot, _deal_expiry_task, _gram_deposit_task
    if _gram_deposit_task is not None:
        _gram_deposit_task.cancel()
        try:
            await _gram_deposit_task
        except asyncio.CancelledError:
            pass
        _gram_deposit_task = None
    if _deal_expiry_task is not None:
        _deal_expiry_task.cancel()
        try:
            await _deal_expiry_task
        except asyncio.CancelledError:
            pass
        _deal_expiry_task = None
    if _telegram_bot is not None:
        await _telegram_bot.session.close()
        _telegram_bot = None

