"""Read-only Telegram Business gift inventory via the official Bot API."""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

TELEGRAM_API_BASE = "https://api.telegram.org"
PAGE_SIZE = 100
MAX_ITEMS = 500


class TelegramBusinessGiftInventoryError(ValueError):
    """Telegram Business returned an unusable or incomplete inventory response."""


def normalise_business_owned_gift(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict) or raw.get("type") != "unique":
        return None
    gift = raw.get("gift")
    if not isinstance(gift, dict):
        return None
    if gift.get("is_from_blockchain") is True or gift.get("is_burned") is True:
        return None
    owned_gift_id = str(raw.get("owned_gift_id") or "").strip()
    slug = str(gift.get("name") or "").strip()
    number = gift.get("number")
    if (not owned_gift_id or len(owned_gift_id) > 256 or not slug
            or len(slug) > 180 or any(ch.isspace() for ch in slug)
            or isinstance(number, bool) or not isinstance(number, int) or number < 1):
        return None
    base_name = str(gift.get("base_name") or "Telegram Gift").strip()[:80]
    model = gift.get("model") if isinstance(gift.get("model"), dict) else {}
    sticker = model.get("sticker") if isinstance(model.get("sticker"), dict) else {}
    thumbnail = sticker.get("thumbnail") if isinstance(sticker.get("thumbnail"), dict) else {}
    preview_file_id = str(thumbnail.get("file_id") or "").strip()
    if not preview_file_id and sticker.get("is_animated") is not True and sticker.get("is_video") is not True:
        preview_file_id = str(sticker.get("file_id") or "").strip()
    if len(preview_file_id) > 512:
        preview_file_id = ""
    return {
        "owned_gift_id": owned_gift_id,
        "model_sticker_file_id": preview_file_id,
        "external_asset_id": slug,
        "title": f"{base_name} #{number}"[:120],
        "gift_url": f"https://t.me/nft/{quote(slug, safe='')}",
        "send_date": str(raw.get("send_date") or ""),
    }


async def fetch_sticker_preview(
    file_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[bytes, str]:
    """Download a small raster Telegram sticker preview without exposing the bot token."""
    safe_file_id = str(file_id or "").strip()
    if not safe_file_id or len(safe_file_id) > 512:
        raise TelegramBusinessGiftInventoryError("A valid Telegram sticker file is required")
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise TelegramBusinessGiftInventoryError("Telegram Bot API is not configured")
    owns_client = client is None
    session = client or httpx.AsyncClient(
        base_url=TELEGRAM_API_BASE,
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=False,
    )
    try:
        response = await session.post(f"/bot{token}/getFile", json={"file_id": safe_file_id})
        if response.status_code != 200:
            raise TelegramBusinessGiftInventoryError("Telegram sticker preview is unavailable")
        payload = response.json()
        result = payload.get("result") if isinstance(payload, dict) and payload.get("ok") is True else None
        file_path = str(result.get("file_path") or "") if isinstance(result, dict) else ""
        if not file_path or file_path.startswith("/") or ".." in file_path.split("/") or len(file_path) > 512:
            raise TelegramBusinessGiftInventoryError("Telegram returned an invalid sticker file path")
        async with session.stream("GET", f"/file/bot{token}/{file_path}") as download:
            if download.status_code != 200:
                raise TelegramBusinessGiftInventoryError("Telegram sticker preview download failed")
            chunks: list[bytes] = []
            total = 0
            async for chunk in download.aiter_bytes():
                total += len(chunk)
                if total > 2_500_000:
                    raise TelegramBusinessGiftInventoryError("Telegram sticker preview exceeds the size limit")
                chunks.append(chunk)
        data = b"".join(chunks)
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            media_type = "image/webp"
        elif data.startswith(bytes.fromhex("89504e470d0a1a0a")):
            media_type = "image/png"
        elif data.startswith(bytes.fromhex("ffd8ff")):
            media_type = "image/jpeg"
        else:
            raise TelegramBusinessGiftInventoryError("Telegram returned an invalid image payload")
        return data, media_type
    except httpx.HTTPError as exc:
        raise TelegramBusinessGiftInventoryError("Telegram sticker preview request failed") from exc
    finally:
        if owns_client:
            await session.aclose()


async def fetch_business_account_gifts(
    business_connection_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    connection_id = str(business_connection_id or "").strip()
    if not connection_id or len(connection_id) > 256:
        raise TelegramBusinessGiftInventoryError("A valid Telegram Business connection is required")
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise TelegramBusinessGiftInventoryError("Telegram Bot API is not configured")
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
            response = await session.post(
                f"/bot{token}/getBusinessAccountGifts",
                json={
                    "business_connection_id": connection_id,
                    "exclude_from_blockchain": True,
                    "limit": min(PAGE_SIZE, MAX_ITEMS - len(items)),
                    "offset": offset,
                },
            )
            if response.status_code != 200:
                raise TelegramBusinessGiftInventoryError("Telegram Business gift inventory is unavailable")
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                raise TelegramBusinessGiftInventoryError("Telegram Business rejected the gift inventory request")
            result = payload.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("gifts"), list):
                raise TelegramBusinessGiftInventoryError("Telegram Business returned an invalid gift inventory")
            total_count = int(result.get("total_count") or 0)
            for raw in result["gifts"]:
                item = normalise_business_owned_gift(raw)
                if item and item["owned_gift_id"] not in seen:
                    seen.add(item["owned_gift_id"])
                    items.append(item)
            next_offset = str(result.get("next_offset") or "")
            if not next_offset:
                complete = True
                break
            if next_offset == offset:
                raise TelegramBusinessGiftInventoryError("Telegram Business returned a repeated pagination cursor")
            offset = next_offset
        if len(items) >= MAX_ITEMS and total_count > len(items):
            complete = False
    except httpx.HTTPError as exc:
        raise TelegramBusinessGiftInventoryError("Telegram Business gift inventory request failed") from exc
    finally:
        if owns_client:
            await session.aclose()
    return {
        "items": items,
        "complete": complete,
        "total_count": total_count,
        "supported_unique_count": len(items),
    }
