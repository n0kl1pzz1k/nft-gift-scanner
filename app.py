import os
import asyncio
import time
import json
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
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

TONNEL_LISTINGS_URL = "https://gifts2.tonnel.network/api/pageGifts"
MRKT_LISTINGS_URL = "https://api.tgmrkt.io/api/v1/gifts/saling"
_mrkt_cache: dict[str, tuple[float, Any]] = {}
_MRKT_CACHE_SECONDS = 30
_mrkt_lock = asyncio.Lock()
_mrkt_last_request_at = 0.0
_mrkt_runtime_status = "public_endpoint_not_checked"
_tonnel_cache: dict[str, tuple[float, Any]] = {}
_TONNEL_CACHE_SECONDS = 30
_tonnel_lock = asyncio.Lock()
_tonnel_last_request_at = 0.0
_tonnel_runtime_status = "public_endpoint_not_checked"


def source_status() -> dict[str, str]:
    return {
        "portals": "configured_read_only" if os.getenv("PORTALS_API_KEY", "").strip() else "needs_api_key",
        "tonnel": _tonnel_runtime_status,
        "mrkt": _mrkt_runtime_status,
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
            "mrkt": ["public_listings"] if _mrkt_runtime_status == "online_read_only" else [],
        },
        "cross_market_comparison": False,
        "note": "Portals uses documented read-only routes. Tonnel and MRKT are experimental public listing queries without account authentication; either may refuse unauthenticated access. No account sessions or tokens are collected.",
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


def _mrkt_value(row: dict, *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _extract_mrkt_items(payload: Any) -> tuple[list[dict], Any, str | None]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="MRKT returned an unexpected response")
    rows = payload.get("gifts")
    if not isinstance(rows, list):
        rows = payload.get("items")
    if not isinstance(rows, list):
        rows = payload.get("results")
    if not isinstance(rows, list):
        raise HTTPException(status_code=502, detail="MRKT response format was not recognized")
    total_count = _mrkt_value(payload, "totalCount", "total_count", "count")
    next_cursor = _mrkt_value(payload, "cursor", "nextCursor", "next_cursor")
    return [row for row in rows if isinstance(row, dict)], total_count if total_count is not None else len(rows), str(next_cursor) if next_cursor else None


async def _fetch_mrkt_page(limit: int, cursor: str, search: str | None) -> tuple[list[dict], Any, str | None]:
    global _mrkt_last_request_at, _mrkt_runtime_status
    cache_key = f"{limit}:{cursor}:{(search or '').strip().lower()}"
    now = time.monotonic()
    cached = _mrkt_cache.get(cache_key)
    if cached and now - cached[0] < _MRKT_CACHE_SECONDS:
        return cached[1]

    term = (search or "").strip()
    body = {
        "collectionNames": [],
        "modelNames": [],
        "backdropNames": [],
        "symbolNames": [],
        "ordering": None,
        "lowToHigh": True,
        "maxPrice": None,
        "minPrice": None,
        "mintable": None,
        "number": int(term) if term.isdigit() else None,
        "count": limit,
        "cursor": cursor,
        "query": None if term.isdigit() or not term else term,
        "promotedFirst": False,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Referer": "https://cdn.tgmrkt.io/",
    }

    async with _mrkt_lock:
        wait = 1.25 - (time.monotonic() - _mrkt_last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(MRKT_LISTINGS_URL, json=body, headers=headers)
        _mrkt_last_request_at = time.monotonic()

    if response.status_code == 429:
        _mrkt_runtime_status = "rate_limited"
        raise HTTPException(status_code=503, detail="MRKT rate limit reached; retry later")
    if response.status_code in (401, 403):
        _mrkt_runtime_status = "public_access_unavailable"
        raise HTTPException(status_code=503, detail="MRKT requires account-linked authorization; this app does not collect Telegram sessions or tokens")
    if response.is_error:
        _mrkt_runtime_status = "temporarily_unavailable"
        raise HTTPException(status_code=502, detail=f"MRKT returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        _mrkt_runtime_status = "temporarily_unavailable"
        raise HTTPException(status_code=502, detail="MRKT returned invalid JSON")

    rows, total_count, next_cursor = _extract_mrkt_items(payload)
    _mrkt_runtime_status = "online_read_only"
    result = (rows, total_count, next_cursor)
    _mrkt_cache[cache_key] = (time.monotonic(), result)
    return result


@app.get("/api/mrkt/gifts")
async def mrkt_gifts(
    limit: int = Query(default=20, ge=1, le=20),
    cursor: str = Query(default="", max_length=500),
    search: str | None = Query(default=None, max_length=80),
):
    """Experimental public MRKT listing query. Never requests Telegram account auth or tokens."""
    rows, total_count, next_cursor = await _fetch_mrkt_page(limit, cursor, search)
    items = []
    for gift in rows:
        raw_attributes = gift.get("attributes")
        attrs = []
        if isinstance(raw_attributes, list):
            for attr in raw_attributes:
                if isinstance(attr, dict):
                    attrs.append({
                        "type": _mrkt_value(attr, "type", "name", "attributeType"),
                        "value": _mrkt_value(attr, "value", "attributeName", "nameValue"),
                        "rarity": _mrkt_value(attr, "rarity", "percent", "percentage"),
                    })
        for label, keys in (
            ("Model", ("model", "modelName")),
            ("Backdrop", ("backdrop", "backdropName")),
            ("Symbol", ("symbol", "symbolName")),
        ):
            value = _mrkt_value(gift, *keys)
            if value is not None and not any(a.get("type") == label for a in attrs):
                attrs.append({"type": label, "value": value})

        items.append({
            "id": _mrkt_value(gift, "id", "giftId", "gift_id"),
            "name": _mrkt_value(gift, "name", "giftName", "gift_name", "collectionName") or "Telegram Gift",
            "number": _mrkt_value(gift, "number", "giftNumber", "gift_num"),
            "collection_id": _mrkt_value(gift, "collectionId", "collection_id", "collectionName"),
            "image": _mrkt_value(gift, "image", "imageUrl", "photoUrl", "preview", "previewUrl"),
            "price": _mrkt_value(gift, "price", "amount", "priceAmount"),
            "floor_price": None,
            "attributes": attrs,
            "status": _mrkt_value(gift, "status", "saleStatus", "state") or "listed",
            "listed_at": _mrkt_value(gift, "listedAt", "createdAt", "created_at", "date"),
            "source": "MRKT",
            "currency": _mrkt_value(gift, "currency", "asset") or "GRAM",
        })
    return {
        "items": items,
        "total_count": total_count,
        "next_cursor": next_cursor,
        "connected": True,
        "sources": {**source_status(), "mrkt": "online_read_only"},
        "updated_at": int(time.time()),
        "note": "Experimental public listings only. If MRKT requires account authorization, no session or token is requested.",
    }
