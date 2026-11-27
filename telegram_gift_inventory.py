"""Read-only Telegram collectible gift inventory via the official Bot API.

Only unique gifts that are not assigned from the TON blockchain are imported.
Regular gifts do not expose a stable per-item ID for ordinary user accounts,
so they are intentionally excluded rather than assigned a guessed identity.
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

TELEGRAM_API_BASE = "https://api.telegram.org"
PAGE_SIZE = 100
MAX_ITEMS = 500


class TelegramGiftInventoryError(ValueError):
    """Telegram returned an unusable or incomplete inventory response."""


def normalise_owned_gift(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or raw.get("type") != "unique":
        return None
    gift = raw.get("gift")
    if not isinstance(gift, dict):
        return None
    # Blockchain-assigned collectibles belong to the TON import path instead.
    if gift.get("is_from_blockchain") is True or gift.get("is_burned") is True:
        return None
    slug = str(gift.get("name") or "").strip()
    if not slug or len(slug) > 180 or any(ch.isspace() for ch in slug):
        return None
    base_name = str(gift.get("base_name") or "Telegram Gift").strip()[:80]
    number = gift.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        return None
    traits: dict[str, Any] = {}
    for key in ("model", "symbol"):
        component = gift.get(key) if isinstance(gift.get(key), dict) else {}
        name = component.get("name")
        rarity = component.get("rarity_per_mille")
        if isinstance(name, str) and name.strip():
            trait: dict[str, Any] = {"name": name.strip()[:100]}
            if isinstance(rarity, int) and not isinstance(rarity, bool) and 0 <= rarity <= 1000:
                trait["rarity_per_mille"] = rarity
            tier = component.get("rarity")
            if key == "model" and tier in {"uncommon", "rare", "epic", "legendary"}:
                trait["rarity"] = tier
            traits[key] = trait
    backdrop = gift.get("backdrop") if isinstance(gift.get("backdrop"), dict) else {}
    backdrop_name = backdrop.get("name")
    if isinstance(backdrop_name, str) and backdrop_name.strip():
        trait = {"name": backdrop_name.strip()[:100]}
        rarity = backdrop.get("rarity_per_mille")
        if isinstance(rarity, int) and not isinstance(rarity, bool) and 0 <= rarity <= 1000:
            trait["rarity_per_mille"] = rarity
        colors = backdrop.get("colors") if isinstance(backdrop.get("colors"), dict) else {}
        safe_colors = {key: value for key, value in colors.items()
                       if key in {"center_color", "edge_color", "symbol_color", "text_color"}
                       and isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 0xFFFFFF}
        if safe_colors:
            trait["colors"] = safe_colors
        traits["backdrop"] = trait
    return {
        "traits": traits,
        "number": number,
        "external_asset_id": slug,
        "title": f"{base_name} #{number}"[:120],
        "asset_type": "gift",
        "image_url": "",
        "gift_url": f"https://t.me/nft/{quote(slug, safe='')}",
        "send_date": str(raw.get("send_date") or ""),
    }


async def fetch_user_telegram_gifts(
    user_id: str | int,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch the user's current Telegram gifts using the server-held bot token."""
    telegram_id = str(user_id).strip()
    if not telegram_id.isdigit():
        raise TelegramGiftInventoryError("A valid Telegram user ID is required")
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise TelegramGiftInventoryError("Telegram Bot API is not configured")

    owns_client = client is None
    session = client or httpx.AsyncClient(
        base_url=TELEGRAM_API_BASE,
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=False,
    )
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    offset = ""
    complete = False
    total_count = 0
    try:
        while len(items) < MAX_ITEMS:
            payload: dict[str, Any] = {
                "user_id": int(telegram_id),
                "limit": min(PAGE_SIZE, MAX_ITEMS - len(items)),
                "exclude_from_blockchain": True,
                "offset": offset,
            }
            response = await session.post(f"/bot{token}/getUserGifts", json=payload)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or body.get("ok") is not True:
                description = body.get("description", "Telegram Bot API request failed") if isinstance(body, dict) else "Invalid Telegram Bot API response"
                raise TelegramGiftInventoryError(str(description)[:240])
            result = body.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("gifts"), list):
                raise TelegramGiftInventoryError("Telegram returned an invalid gift inventory")
            total_count = int(result.get("total_count") or total_count or 0)
            for raw in result["gifts"]:
                item = normalise_owned_gift(raw)
                if item and item["external_asset_id"] not in seen:
                    seen.add(item["external_asset_id"])
                    items.append(item)
            next_offset = str(result.get("next_offset") or "")
            if not next_offset:
                complete = True
                break
            if next_offset == offset:
                raise TelegramGiftInventoryError("Telegram gift pagination did not advance")
            offset = next_offset
        return {
            "items": items,
            "complete": complete,
            "total_count": total_count,
            "supported_unique_count": len(items),
        }
    finally:
        if owns_client:
            await session.aclose()
