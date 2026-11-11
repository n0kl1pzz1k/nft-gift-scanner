import os
import asyncio
import time
import json
import hmac
import hashlib
import secrets
from urllib.parse import parse_qsl
from pydantic import BaseModel
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Header, Request
from fastapi.middleware.cors import CORSMiddleware

from marketplace import create_marketplace_router, get_app_setting, init_marketplace_db, set_app_setting, upsert_telegram_user
from demo_offers import create_demo_offers_router, init_demo_offers_db
from catalog_store import get_catalogue_history, get_catalogue_item, search_catalogue_items, upsert_catalogue_items

app = FastAPI(title="GIFT RADAR API", version="0.4.0")
app.include_router(create_marketplace_router(lambda value: _verify_telegram_init_data(value)))
app.include_router(create_demo_offers_router(lambda value: _verify_telegram_init_data(value)))

_telegram_bot = None
_telegram_dispatcher = None
_telegram_webhook_secret = None

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
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Telegram-Init-Data"],
    max_age=600,
)

PORTALS_BASE = "https://portal-market.com/partners"
ALLOWED_PORTALS_PATHS = {
    "/collections/preview?sort_by=name&sort_order=asc",
    "/collections/floors",
}
_cache: dict[str, tuple[float, Any]] = {}
CACHE_SECONDS = 60
# Per-query cache for item listings. The browser always reads from this backend;
# upstream marketplaces are queried at a controlled cadence, not on every tap.
_gifts_cache: dict[str, tuple[float, Any]] = {}
GIFTS_CACHE_SECONDS = 60


def _safe_gift_attributes(raw_attributes: Any) -> list[dict[str, str]]:
    """Keep upstream attribute payloads small and aligned with the Mini App schema."""
    if not isinstance(raw_attributes, list):
        return []
    safe: list[dict[str, str]] = []
    for attribute in raw_attributes[:40]:
        if not isinstance(attribute, dict):
            continue
        normalized: dict[str, str] = {}
        for field in ("type", "name", "value"):
            value = attribute.get(field)
            if value is None or isinstance(value, (dict, list)):
                normalized[field] = ""
            else:
                normalized[field] = str(value).strip()[:160]
        if any(normalized.values()):
            safe.append(normalized)
    return safe


def _safe_total_count(reported_total: Any, item_count: int) -> int:
    try:
        return max(item_count, int(reported_total or 0))
    except (TypeError, ValueError):
        return item_count
_request_lock = asyncio.Lock()
_last_request_at = 0.0
_refresh_task: asyncio.Task | None = None


def _refresh_interval_seconds() -> int:
    try:
        return max(60, min(int(os.getenv("DATA_REFRESH_SECONDS", "60")), 900))
    except (TypeError, ValueError):
        return 60


class TelegramAuthRequest(BaseModel):
    init_data: str


class MarketplaceSettingRequest(BaseModel):
    enabled: bool


# Admin controls are limited to catalogue visibility; the setting is persisted in SQLite.
# Administrator identities are verified server-side from Telegram initData and env configuration.
def _marketplace_visibility() -> dict[str, bool]:
    return {"portals": get_app_setting("marketplace.portals.enabled", "true").lower() == "true"}


def _marketplace_enabled(market: str) -> bool:
    return _marketplace_visibility().get(market, False)


def _admin_ids() -> set[str]:
    raw = os.getenv("ADMIN_TELEGRAM_IDS", os.getenv("ADMIN_TELEGRAM_ID", ""))
    return {part.strip() for part in raw.split(",") if part.strip().isdigit()}


def _verify_telegram_init_data(init_data: str) -> dict:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise HTTPException(status_code=503, detail="Telegram authentication is not configured")
    if not init_data or len(init_data) > 8192:
        raise HTTPException(status_code=401, detail="Missing or invalid Telegram initData")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
        values = dict(pairs)
        received_hash = values.pop("hash", "")
        if not received_hash or len(values) != len(pairs) - 1:
            raise ValueError("Invalid initData fields")
        check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash):
            raise ValueError("Invalid Telegram signature")
        auth_date = int(values.get("auth_date", "0"))
        now = int(time.time())
        if auth_date <= 0 or auth_date > now + 60 or now - auth_date > 86400:
            raise ValueError("Expired Telegram initData")
        user = json.loads(values.get("user", "{}"))
        if not isinstance(user, dict) or not str(user.get("id", "")).isdigit():
            raise ValueError("Telegram user is missing")
        return user
    except (ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="Telegram authentication failed; reopen the Mini App")


def _require_admin(init_data: str | None) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="Telegram Mini App authentication required")
    user = _verify_telegram_init_data(init_data)
    if str(user["id"]) not in _admin_ids():
        raise HTTPException(status_code=403, detail="Administrator access only")
    return user


@app.post("/api/auth/telegram")
async def telegram_auth(payload: TelegramAuthRequest):
    user = _verify_telegram_init_data(payload.init_data)
    profile = upsert_telegram_user(user)
    is_admin = str(user["id"]) in _admin_ids()
    return {
        "authenticated": True,
        "is_admin": is_admin,
        "user": profile,
    }


@app.get("/api/admin/overview")
async def admin_overview(x_telegram_init_data: str | None = Header(default=None)):
    user = _require_admin(x_telegram_init_data)
    return {
        "admin": {"id": str(user["id"]), "first_name": user.get("first_name", ""), "username": user.get("username", "")},
        "mode": "catalogue_read_only_with_shared_demo_marketplace",
        "sources": source_status(),
        "marketplace_visibility": _marketplace_visibility(),
        "settings_persistence": "persistent_sqlite",
    }


@app.post("/api/admin/marketplaces/{market}")
async def admin_set_marketplace(
    market: str,
    payload: MarketplaceSettingRequest,
    x_telegram_init_data: str | None = Header(default=None),
):
    _require_admin(x_telegram_init_data)
    if market != "portals":
        raise HTTPException(status_code=404, detail="Unknown marketplace")
    set_app_setting(f"marketplace.{market}.enabled", "true" if payload.enabled else "false")
    return {"market": market, "enabled": _marketplace_enabled(market)}



def source_status() -> dict[str, str]:
    return {
        "portals": "configured_read_only" if os.getenv("PORTALS_API_KEY", "").strip() else "needs_api_key",
        "gift_changes": "public_read_only",
        "tg_gifts_daily": "public_daily_read_only",
    }


async def _portals_get_json(route: str, params: dict[str, Any] | None = None) -> dict:
    """Perform a rate-limited, retryable read-only request to the Portals Partners API."""
    if route not in {"/collections/preview", "/collections/floors", "/nfts/search"}:
        raise HTTPException(status_code=400, detail="Portals route is not enabled")
    token = os.getenv("PORTALS_API_KEY", "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="PORTALS_API_KEY is not configured")

    headers = {"Authorization": f"partners {token}", "Accept": "application/json"}
    global _last_request_at
    last_status: int | None = None

    for attempt in range(3):
        try:
            async with _request_lock:
                wait = 1.05 - (time.monotonic() - _last_request_at)
                if wait > 0:
                    await asyncio.sleep(wait)
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(
                        f"{PORTALS_BASE}{route}",
                        headers=headers,
                        params=params,
                    )
                _last_request_at = time.monotonic()
        except httpx.HTTPError:
            if attempt < 2:
                await asyncio.sleep(1.05 * (attempt + 1))
                continue
            raise HTTPException(status_code=503, detail="Portals API is temporarily unreachable")

        last_status = response.status_code
        if response.status_code == 429:
            retry_after_raw = response.headers.get("Retry-After", "1.05")
            try:
                retry_after = max(1.05, float(retry_after_raw))
            except (TypeError, ValueError):
                retry_after = 1.05
            if attempt < 2 and retry_after <= 15:
                await asyncio.sleep(retry_after)
                continue
            raise HTTPException(
                status_code=503,
                detail="Portals rate limit reached; retry shortly",
                headers={"Retry-After": str(max(1, int(retry_after)))},
            )
        if response.status_code in {500, 502, 503, 504} and attempt < 2:
            await asyncio.sleep(1.05 * (attempt + 1))
            continue
        if response.status_code == 401:
            raise HTTPException(status_code=502, detail="Portals rejected the API key")
        if response.status_code == 403:
            raise HTTPException(status_code=502, detail="Portals API subscription or access is not active")
        if response.is_error:
            raise HTTPException(status_code=502, detail=f"Portals API error: HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise HTTPException(status_code=502, detail="Portals returned invalid JSON")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="Portals returned an unexpected response")
        return payload

    raise HTTPException(
        status_code=503,
        detail=f"Portals API temporarily unavailable (HTTP {last_status or 'network error'})",
    )


async def portals_get(path: str) -> dict:
    # Keep the legacy internal path contract while enforcing an explicit read-only route allowlist.
    if path not in ALLOWED_PORTALS_PATHS:
        raise HTTPException(status_code=400, detail="Portals route is not enabled")
    route, _, query = path.partition("?")
    params = dict(parse_qsl(query, keep_blank_values=True))
    return await _portals_get_json(route, params or None)


async def cached_portals(path: str) -> dict:
    now = time.monotonic()
    cached = _cache.get(path)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    data = await portals_get(path)
    _cache[path] = (time.monotonic(), data)
    return data


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
    from aiogram.types import Update
    try:
        payload = await request.json()
        update = Update.model_validate(payload, context={"bot": _telegram_bot})
        await _telegram_dispatcher.feed_update(_telegram_bot, update)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Telegram update")
    return {"ok": True}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "nft-gift-scanner"}


@app.get("/api/status")
async def status():
    return {
        "mode": "catalogue_read_only_with_shared_demo_marketplace",
        "sources": source_status(),
        "available_data": {
            "portals": ["collection_list", "collection_floor", "reported_24h_volume", "reported_supply", "listed_gift_catalog", "gift_price", "gift_attributes"],
            "gift_changes": ["public_gift_metadata_changes_beta"],
            "tg_gifts_daily": ["daily_collection_floor_history_cc_by_4_0"],
        },
        "marketplace_visibility": _marketplace_visibility(),
        "cross_market_comparison": False,
        "data_pipeline": {
            "delivery": "server_to_app_api",
            "cache": "server_memory_cache",
            "refresh_interval_seconds": _refresh_interval_seconds(),
            "persistent_history": True,
            "history_kind": "portals_metadata_snapshots",
            "history_limit_per_gift": 50,
            "history_requires_persistent_volume_for_deploys": True,
        },
        "demo_marketplace": {
            "enabled": True,
            "storage": "sqlite_server_database",
            "payments": False,
            "wallet_operations": False,
            "blockchain_transactions": False,
        },
        "note": "Portals is the sole marketplace catalogue. Independent public reference feeds provide beta gift-metadata changes and daily historical collection floors; these are not live listings.",
    }


@app.get("/api/collections")
async def collections():
    if not _marketplace_enabled("portals"):
        return {"items": [], "sources": source_status(), "updated_at": None, "note": "Portals collection feed is disabled by the administrator."}
    if not os.getenv("PORTALS_API_KEY", "").strip():
        return {
            "items": [],
            "sources": source_status(),
            "updated_at": None,
            "note": "Portals data is not connected. No market values are fabricated.",
        }

    try:
        preview, floors = await asyncio.gather(
            cached_portals("/collections/preview?sort_by=name&sort_order=asc"),
            cached_portals("/collections/floors"),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Could not load Portals market data")

    floor_map = floors.get("floorPrices", {})
    if not isinstance(floor_map, dict):
        floor_map = {}
    collections_data = preview.get("collections", [])
    if not isinstance(collections_data, list):
        raise HTTPException(status_code=502, detail="Portals collections response is invalid")

    items = []
    for item in collections_data:
        if not isinstance(item, dict):
            continue
        short_name = item.get("short_name")
        floor = floor_map.get(short_name, item.get("floor_price"))
        items.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "short_name": short_name,
            "image": item.get("photo_url"),
            "floor_price": floor,
            "day_volume": item.get("day_volume"),
            "supply": item.get("supply"),
            "source": "Portals",
        })

    return {
        "items": items,
        "sources": {
            **source_status(),
            "portals": "online_read_only",
        },
        "updated_at": int(time.time()),
        "note": "These are Portals collection-level figures, not item-level listings or cross-market price matches.",
    }


@app.get("/api/gifts")
async def gifts(
    limit: int = Query(default=24, ge=1, le=40),
    offset: int = Query(default=0, ge=0, le=10000),
    search: str | None = Query(default=None, max_length=80),
    collection_id: str | None = Query(default=None, max_length=64),
    min_price: str | None = Query(default=None, max_length=32),
    max_price: str | None = Query(default=None, max_length=32),
    external_collection_number: str | None = Query(default=None, max_length=16),
    filter_by_backdrops: str | None = Query(default=None, max_length=100),
    filter_by_models: str | None = Query(default=None, max_length=100),
    filter_by_symbols: str | None = Query(default=None, max_length=100),
    mint_status: str | None = Query(default=None, max_length=16),
    statuses: str | None = Query(default=None, max_length=100),
    sort_by: str | None = Query(default=None, max_length=40),
    status: str | None = Query(default=None, max_length=16),
):
    """Read-only page of currently listed gifts from the documented Portals search endpoint."""
    if not _marketplace_enabled("portals"):
        raise HTTPException(status_code=503, detail="Portals catalogue is temporarily disabled by the administrator")
    if not os.getenv("PORTALS_API_KEY", "").strip():
        return {"items": [], "total_count": 0, "connected": False, "sources": source_status(), "updated_at": None}

    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if search and search.strip():
        params["search"] = search.strip()
    if collection_id and collection_id.strip():
        params["collection_id"] = collection_id.strip()
    for key, value in (("min_price", min_price), ("max_price", max_price)):
        if value and value.strip():
            try:
                amount = Decimal(value.strip())
                if not amount.is_finite() or amount < 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                raise HTTPException(status_code=422, detail=f"{key} must be a non-negative decimal")
            params[key] = value.strip()
    if min_price and min_price.strip() and max_price and max_price.strip() and Decimal(min_price.strip()) > Decimal(max_price.strip()):
        raise HTTPException(status_code=422, detail="min_price cannot exceed max_price")
    if external_collection_number and external_collection_number.strip():
        if not external_collection_number.strip().isdigit():
            raise HTTPException(status_code=422, detail="external_collection_number must contain digits only")
        params["external_collection_number"] = external_collection_number.strip()
    if filter_by_backdrops and filter_by_backdrops.strip():
        params["filter_by_backdrops"] = filter_by_backdrops.strip()
    if filter_by_models and filter_by_models.strip():
        params["filter_by_models"] = filter_by_models.strip()
    if filter_by_symbols and filter_by_symbols.strip():
        params["filter_by_symbols"] = filter_by_symbols.strip()
    if mint_status:
        if mint_status not in {"all", "minted", "not_minted"}:
            raise HTTPException(status_code=422, detail="mint_status must be all, minted or not_minted")
        params["mint_status"] = mint_status
    if statuses and statuses.strip():
        allowed_statuses = {"listed", "unlisted", "withdrawn", "auction", "deleted"}
        requested_statuses = [value.strip() for value in statuses.split(",") if value.strip()]
        if not requested_statuses or any(value not in allowed_statuses for value in requested_statuses):
            raise HTTPException(status_code=422, detail="statuses contains an unsupported value")
        params["statuses"] = ",".join(requested_statuses)
    allowed_sorts = {
        "listed_at desc", "price asc", "price desc",
        "external_collection_number asc", "external_collection_number desc",
        "model_rarity asc", "model_rarity desc", "name asc", "name desc",
    }
    if sort_by:
        if sort_by not in allowed_sorts:
            raise HTTPException(status_code=422, detail="Unsupported sort_by value")
        params["sort_by"] = sort_by
    if status:
        if status not in {"listed", "unlisted"}:
            raise HTTPException(status_code=422, detail="status must be listed or unlisted")
        params["status"] = status

    # Reuse the last server-side result for identical filters.
    cache_key = json.dumps(params, sort_keys=True, separators=(",", ":"))
    cached = _gifts_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < GIFTS_CACHE_SECONDS:
        return cached[1]

    try:
        payload = await _portals_get_json("/nfts/search", params)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Could not load Portals gift listings")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Portals gift search response is invalid")
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise HTTPException(status_code=502, detail="Portals gift search response is invalid")

    items = []
    for gift in results:
        if not isinstance(gift, dict):
            continue
        items.append({
            "id": gift.get("id"),
            "name": gift.get("name"),
            "number": gift.get("external_collection_number"),
            "collection_id": gift.get("collection_id"),
            "image": gift.get("photo_url"),
            "animation_url": gift.get("animation_url") or gift.get("animationUrl"),
            "has_animation": bool(gift.get("has_animation") or gift.get("hasAnimation") or gift.get("animation_url") or gift.get("animationUrl")),
            "price": gift.get("price"),
            "floor_price": gift.get("floor_price"),
            "attributes": _safe_gift_attributes(gift.get("attributes")),
            "status": gift.get("status"),
            "listed_at": gift.get("listed_at"),
            "source": "Portals",
            "currency": "GRAM",
        })

    result = {
        "items": items,
        "total_count": _safe_total_count(payload.get("total_count"), len(items)),
        "connected": True,
        "sources": {**source_status(), "portals": "online_read_only"},
        "updated_at": int(time.time()),
    }
    # Persist only authoritative Portals catalogue metadata. This is a metadata index,
    # not custody, ownership proof, or a transfer of the underlying NFT.
    upsert_catalogue_items(items)
    _gifts_cache[cache_key] = (time.monotonic(), result)
    if len(_gifts_cache) > 200:
        oldest = sorted(_gifts_cache, key=lambda key: _gifts_cache[key][0])[:-150]
        for old_key in oldest:
            _gifts_cache.pop(old_key, None)
    return result



@app.get("/api/catalog/items/{gift_id}")
async def catalogue_item(gift_id: str):
    """Return the latest Portals metadata snapshot previously indexed by GIFT RADAR."""
    item = get_catalogue_item(gift_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Gift is not present in the indexed Portals catalogue")
    return {"item": item, "source": "Portals", "metadata_only": True}


@app.get("/api/catalog/items")
async def catalogue_items(
    limit: int = Query(default=24, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=100000),
    search: str | None = Query(default=None, max_length=80),
    collection_id: str | None = Query(default=None, max_length=160),
):
    """Search the persistent Portals metadata index; results are not ownership claims."""
    return {**search_catalogue_items(search or "", collection_id or "", limit, offset), "source": "Portals", "metadata_only": True}


@app.get("/api/catalog/items/{gift_id}/history")
async def catalogue_item_history(
    gift_id: str,
    limit: int = Query(default=20, ge=1, le=50),
):
    """Return previous Portals metadata snapshots for an indexed item."""
    if get_catalogue_item(gift_id) is None:
        raise HTTPException(status_code=404, detail="Gift is not present in the indexed Portals catalogue")
    return {"gift_id": gift_id, "history": get_catalogue_history(gift_id, limit), "metadata_only": True}


_REFERENCE_CACHE: dict[str, tuple[float, Any]] = {}
_REFERENCE_CACHE_SECONDS = 3600
CHANGES_API_BASE = "https://api.changes.tg"
TG_GIFTS_DAILY_URL = "https://tggifts.com/data/floors-daily.json"


async def _fetch_reference_json(cache_key: str, url: str, params: dict[str, Any] | None = None,
                                cache_seconds: int = _REFERENCE_CACHE_SECONDS) -> Any:
    cached = _REFERENCE_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < cache_seconds:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(url, params=params, headers={
                "Accept": "application/json",
                "User-Agent": "GIFT-RADAR/1.0 (public read-only data)",
            })
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Public reference data source is unreachable")
    if response.status_code == 429:
        raise HTTPException(status_code=503, detail="Public reference data rate limit reached; retry later")
    if response.is_error:
        raise HTTPException(status_code=502, detail=f"Public reference source returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Public reference source returned invalid JSON")
    _REFERENCE_CACHE[cache_key] = (time.monotonic(), payload)
    if len(_REFERENCE_CACHE) > 100:
        for old_key in sorted(_REFERENCE_CACHE, key=lambda key: _REFERENCE_CACHE[key][0])[:-60]:
            _REFERENCE_CACHE.pop(old_key, None)
    return payload


@app.get("/api/reference/gift-changes")
async def gift_reference_changes(limit: int = Query(default=25, ge=1, le=100),
                                 since: str | None = Query(default=None, max_length=80)):
    """Recent public gift metadata changes (beta feed; not marketplace listings)."""
    params: dict[str, Any] = {"limit": limit}
    if since and since.strip():
        params["since"] = since.strip()
    cache_key = "gift_changes:" + json.dumps(params, sort_keys=True)
    payload = await _fetch_reference_json(cache_key, f"{CHANGES_API_BASE}/changes",
                                          params=params, cache_seconds=120)
    return {
        "data": payload, "source": "Gift Changes API", "source_url": "https://api.changes.tg/",
        "updated_at": int(time.time()),
        "note": "Beta metadata-change feed. It does not contain marketplace listing availability or prices.",
    }


@app.get("/api/reference/daily-floors")
async def reference_daily_floors(full: bool = Query(default=False)):
    """Daily historical floors; summary by default, full upstream dataset on explicit request."""
    payload = await _fetch_reference_json("tg_gifts_daily_floors", TG_GIFTS_DAILY_URL,
                                          cache_seconds=3600)
    collections_data = payload.get("collections", {}) if isinstance(payload, dict) else {}
    if not isinstance(collections_data, dict):
        collections_data = {}
    latest_dates: list[str] = []
    observation_count = 0
    for collection in collections_data.values():
        if not isinstance(collection, dict):
            continue
        for field in ("days", "timed_sale_days"):
            points = collection.get(field, [])
            if isinstance(points, list):
                observation_count += len(points)
                latest_dates.extend(str(point[0]) for point in points
                                    if isinstance(point, (list, tuple)) and point)
    timed = payload.get("timed_sale_collections", {}) if isinstance(payload, dict) else {}
    timed_items = timed.get("items", []) if isinstance(timed, dict) else []
    summary = {
        "generated": payload.get("generated") if isinstance(payload, dict) else None,
        "collection_count": len(collections_data),
        "observation_count": observation_count,
        "latest_snapshot_date": max(latest_dates) if latest_dates else None,
        "timed_sale_collection_count": len(timed_items) if isinstance(timed_items, list) else 0,
    }
    return {
        "data": payload if full else summary,
        "full_dataset_available": True,
        "source": "TG Gifts", "source_url": "https://tggifts.com/data",
        "attribution": "Data: TG Gifts (tggifts.com)", "license": "CC BY 4.0",
        "updated_at": int(time.time()),
        "note": "Daily historical snapshots, not live marketplace listings or a real-time floor.",
    }


async def _refresh_public_catalogues():
    """Warm backend caches from upstream public APIs independently of browser visits."""
    interval = _refresh_interval_seconds()
    while True:
        if _marketplace_enabled("portals") and os.getenv("PORTALS_API_KEY", "").strip():
            try:
                await collections()
            except Exception:
                pass
            try:
                await gifts(
                    limit=24,
                    offset=0,
                    search=None,
                    collection_id=None,
                    min_price=None,
                    max_price=None,
                    external_collection_number=None,
                    filter_by_backdrops=None,
                    filter_by_models=None,
                    filter_by_symbols=None,
                    mint_status=None,
                    statuses=None,
                    sort_by=None,
                    status=None,
                )
            except Exception:
                pass
        await asyncio.sleep(interval)


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

