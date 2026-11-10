import os
import asyncio
import time
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


def source_status() -> dict[str, str]:
    return {
        "portals": "configured_read_only" if os.getenv("PORTALS_API_KEY", "").strip() else "needs_api_key",
        "tonnel": "official_api_not_verified",
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
            "tonnel": [],
            "mrkt": [],
        },
        "cross_market_comparison": False,
        "note": "Only verified source data is displayed. Cross-market comparisons stay disabled until official read-only data access is verified for each marketplace.",
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
):
    """Read-only page of currently listed gifts from the documented Portals search endpoint."""
    if not os.getenv("PORTALS_API_KEY", "").strip():
        return {"items": [], "total_count": 0, "connected": False, "sources": source_status(), "updated_at": None}

    params: dict[str, Any] = {"limit": limit, "offset": offset, "status": "listed"}
    if search and search.strip():
        params["search"] = search.strip()
    if collection_id and collection_id.strip():
        params["collection_id"] = collection_id.strip()

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
