import os
import asyncio
import time
import json
import hmac
import hashlib
from urllib.parse import parse_qsl
from pydantic import BaseModel
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="NFT Gift Scanner API", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

PORTALS_BASE = "https://portal-market.com/partners"
ALLOWED_PORTALS_PATHS = {
    "/collections/preview?sort_by=name&sort_order=asc",
    "/collections/floors",
}
_cache: dict[str, tuple[float, Any]] = {}
CACHE_SECONDS = 30
_request_lock = asyncio.Lock()
_last_request_at = 0.0



class TelegramAuthRequest(BaseModel):
    init_data: str


class MarketplaceSettingRequest(BaseModel):
    enabled: bool


# Runtime controls are deliberately limited to read-only market visibility.
# For durable configuration across deploys, set ADMIN_TELEGRAM_IDS in Railway.
_marketplace_visibility = {"portals": True, "tonnel": True, "mrkt": False}


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
        if auth_date <= 0 or abs(int(time.time()) - auth_date) > 86400:
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
    is_admin = str(user["id"]) in _admin_ids()
    return {
        "authenticated": True,
        "is_admin": is_admin,
        "user": {
            "id": str(user["id"]),
            "first_name": user.get("first_name", ""),
            "username": user.get("username", ""),
            "photo_url": user.get("photo_url", ""),
        },
    }


@app.get("/api/admin/overview")
async def admin_overview(x_telegram_init_data: str | None = Header(default=None)):
    user = _require_admin(x_telegram_init_data)
    return {
        "admin": {"id": str(user["id"]), "first_name": user.get("first_name", ""), "username": user.get("username", "")},
        "mode": "read_only",
        "sources": source_status(),
        "marketplace_visibility": dict(_marketplace_visibility),
        "settings_persistence": "runtime_only_resets_on_backend_restart",
    }


@app.post("/api/admin/marketplaces/{market}")
async def admin_set_marketplace(
    market: str,
    payload: MarketplaceSettingRequest,
    x_telegram_init_data: str | None = Header(default=None),
):
    _require_admin(x_telegram_init_data)
    if market not in _marketplace_visibility:
        raise HTTPException(status_code=404, detail="Unknown marketplace")
    _marketplace_visibility[market] = bool(payload.enabled)
    return {"market": market, "enabled": _marketplace_visibility[market]}



TONNEL_LISTINGS_URL = "https://gifts2.tonnel.network/api/pageGifts"
_tonnel_cache: dict[str, tuple[float, Any]] = {}
_TONNEL_CACHE_SECONDS = 30
_tonnel_lock = asyncio.Lock()
_tonnel_last_request_at = 0.0
_tonnel_runtime_status = "public_endpoint_not_checked"


def source_status() -> dict[str, str]:
    return {
        "portals": "configured_read_only" if os.getenv("PORTALS_API_KEY", "").strip() else "needs_api_key",
        "tonnel": _tonnel_runtime_status,
        "mrkt": "official_api_not_verified",
    }


async def portals_get(path: str) -> dict:
    # Only explicitly allowlisted, read-only market-data GET routes are enabled.
    if path not in ALLOWED_PORTALS_PATHS:
        raise HTTPException(status_code=400, detail="Portals route is not enabled")
    token = os.getenv("PORTALS_API_KEY", "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="PORTALS_API_KEY is not configured")
    headers = {"Authorization": f"partners {token}"}
    global _last_request_at
    async with _request_lock:
        wait = 1.05 - (time.monotonic() - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{PORTALS_BASE}{path}", headers=headers)
        _last_request_at = time.monotonic()
    if response.status_code == 401:
        raise HTTPException(status_code=502, detail="Portals rejected the API key")
    if response.status_code == 429:
        raise HTTPException(status_code=503, detail="Portals rate limit reached; retry shortly")
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


async def cached_portals(path: str) -> dict:
    now = time.monotonic()
    cached = _cache.get(path)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    data = await portals_get(path)
    _cache[path] = (time.monotonic(), data)
    return data


@app.get("/health")
async def health():
    return {"status": "ok", "service": "nft-gift-scanner"}


@app.get("/api/status")
async def status():
    return {
        "mode": "read_only",
        "sources": source_status(),
        "available_data": {
            "portals": ["collection_list", "collection_floor", "reported_24h_volume", "reported_supply", "listed_gift_catalog", "gift_price", "gift_attributes"],
            "tonnel": ["public_listings"] if _tonnel_runtime_status == "online_read_only" else [],
            "mrkt": [],
        },
        "marketplace_visibility": dict(_marketplace_visibility),
        "cross_market_comparison": False,
        "note": "Portals uses documented read-only routes. Tonnel uses an experimental public-listings endpoint and may be unavailable. MRKT remains unconnected because the available integration instructions require account-linked Telegram authorization.",
    }


@app.get("/api/collections")
async def collections():
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
    if not _marketplace_visibility["portals"]:
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

    token = os.getenv("PORTALS_API_KEY", "").strip()
    headers = {"Authorization": f"partners {token}"}
    global _last_request_at
    try:
        async with _request_lock:
            wait = 1.05 - (time.monotonic() - _last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{PORTALS_BASE}/nfts/search",
                    headers=headers,
                    params=params,
                )
            _last_request_at = time.monotonic()
        if response.status_code == 401:
            raise HTTPException(status_code=502, detail="Portals rejected the API key")
        if response.status_code == 429:
            raise HTTPException(status_code=503, detail="Portals rate limit reached; retry shortly")
        if response.status_code == 403:
            raise HTTPException(status_code=502, detail="Portals API access is not active")
        if response.is_error:
            raise HTTPException(status_code=502, detail=f"Portals API error: HTTP {response.status_code}")
        payload = response.json()
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
            "price": gift.get("price"),
            "floor_price": gift.get("floor_price"),
            "attributes": gift.get("attributes", []),
            "status": gift.get("status"),
            "listed_at": gift.get("listed_at"),
            "source": "Portals",
            "currency": "GRAM",
        })

    return {
        "items": items,
        "total_count": payload.get("total_count", len(items)),
        "connected": True,
        "sources": {**source_status(), "portals": "online_read_only"},
        "updated_at": int(time.time()),
    }



def _extract_tonnel_items(payload: Any) -> list[dict]:
    """Accept the list and common wrapper shapes returned by the public Tonnel listing endpoint."""
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = None
        found_list = False
        for key in ("data", "items", "results", "gifts"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                rows = candidate
                found_list = True
                break
            if isinstance(candidate, dict):
                for nested_key in ("items", "results", "gifts"):
                    nested = candidate.get(nested_key)
                    if isinstance(nested, list):
                        rows = nested
                        found_list = True
                        break
            if rows is not None:
                break
        if rows is None:
            rows = []
        if not found_list:
            raise HTTPException(status_code=502, detail="Tonnel response format was not recognized")
    else:
        raise HTTPException(status_code=502, detail="Tonnel returned an unexpected response")
    return [row for row in rows if isinstance(row, dict)]


async def _fetch_tonnel_page(page: int, limit: int, search: str | None) -> tuple[list[dict], Any]:
    global _tonnel_last_request_at, _tonnel_runtime_status
    cache_key = f"{page}:{limit}:{(search or '').strip().lower()}"
    now = time.monotonic()
    cached = _tonnel_cache.get(cache_key)
    if cached and now - cached[0] < _TONNEL_CACHE_SECONDS:
        return cached[1]

    filters: dict[str, Any] = {
        "price": {"$exists": True},
        "refunded": {"$ne": True},
        "buyer": {"$exists": False},
        "export_at": {"$exists": True},
        "asset": "TON",
    }
    term = (search or "").strip()
    if term:
        if term.isdigit():
            filters["gift_num"] = int(term)
        else:
            filters["gift_name"] = term

    body = {
        "page": page,
        "limit": limit,
        "sort": json.dumps({"message_post_time": -1, "gift_id": -1}, separators=(",", ":")),
        "filter": json.dumps(filters, ensure_ascii=False, separators=(",", ":")),
        "price_range": None,
        "user_auth": "",
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://market.tonnel.network",
        "Referer": "https://market.tonnel.network/",
        "User-Agent": "NFT-Gift-Scanner/1.0 (public read-only catalogue)",
    }

    async with _tonnel_lock:
        wait = 1.5 - (time.monotonic() - _tonnel_last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.post(TONNEL_LISTINGS_URL, json=body, headers=headers)
        _tonnel_last_request_at = time.monotonic()

    if response.status_code == 429:
        _tonnel_runtime_status = "rate_limited"
        raise HTTPException(status_code=503, detail="Tonnel rate limit reached; retry later")
    if response.status_code in (401, 403):
        _tonnel_runtime_status = "public_access_unavailable"
        raise HTTPException(status_code=503, detail="Tonnel did not allow public unauthenticated listing access")
    if response.is_error:
        _tonnel_runtime_status = "temporarily_unavailable"
        raise HTTPException(status_code=502, detail=f"Tonnel returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        _tonnel_runtime_status = "temporarily_unavailable"
        raise HTTPException(status_code=502, detail="Tonnel returned invalid JSON")

    rows = _extract_tonnel_items(payload)
    total_count = payload.get("total_count", payload.get("count", len(rows))) if isinstance(payload, dict) else len(rows)
    _tonnel_runtime_status = "online_read_only"
    result = (rows, total_count)
    _tonnel_cache[cache_key] = (time.monotonic(), result)
    return result


@app.get("/api/tonnel/gifts")
async def tonnel_gifts(
    limit: int = Query(default=24, ge=1, le=30),
    page: int = Query(default=1, ge=1, le=10000),
    search: str | None = Query(default=None, max_length=80),
):
    """Public Tonnel listings only. No account auth, trading or anti-bot workarounds."""
    if not _marketplace_visibility["tonnel"]:
        raise HTTPException(status_code=503, detail="Tonnel catalogue is temporarily disabled by the administrator")
    rows, total_count = await _fetch_tonnel_page(page, limit, search)
    items = []
    for gift in rows:
        model = gift.get("model")
        backdrop = gift.get("backdrop")
        symbol = gift.get("symbol")
        attrs = []
        if model:
            attrs.append({"type": "Model", "value": model})
        if backdrop:
            attrs.append({"type": "Backdrop", "value": backdrop})
        if symbol:
            attrs.append({"type": "Symbol", "value": symbol})
        items.append({
            "id": gift.get("gift_id"),
            "name": gift.get("name") or gift.get("gift_name") or "Telegram Gift",
            "number": gift.get("gift_num"),
            "image": gift.get("image") or gift.get("photo_url"),
            "price": gift.get("price"),
            "floor_price": None,
            "attributes": attrs,
            "status": gift.get("status") or "forsale",
            "listed_at": gift.get("export_at") or gift.get("message_post_time"),
            "source": "Tonnel",
            "currency": gift.get("asset") or "TON",
        })
    return {
        "items": items,
        "total_count": total_count,
        "connected": True,
        "sources": {**source_status(), "tonnel": "online_read_only"},
        "updated_at": int(time.time()),
        "note": "Public listings only. Floor is not inferred from a partial listings page.",
    }

