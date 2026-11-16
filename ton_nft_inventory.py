"""Read-only TON NFT ownership discovery through the TonAPI indexer.

The connected wallet is first bound to a Telegram user with TON Proof. This
module then queries the indexed on-chain owner field for that exact address.
No client-supplied NFT metadata is accepted.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib.parse import urlparse

import httpx

TONAPI_BASE_URL = "https://tonapi.io"
PAGE_SIZE = 100
MAX_ITEMS = 500


def _raw_address(value: str) -> str:
    from tonsdk.utils import Address

    address = Address(value)
    return f"{int(address.wc)}:{bytes(address.hash_part).hex()}"


def _https_image(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if value.startswith("ipfs://"):
        value = "https://ipfs.io/ipfs/" + value[7:].lstrip("/")
    elif value.startswith("ar://"):
        value = "https://arweave.net/" + value[5:].lstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return ""
    if len(value) > 2048:
        return ""
    return value


def _normalise_nft(item: dict[str, Any], wallet_address: str) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    owner = item.get("owner")
    owner_address = owner.get("address") if isinstance(owner, dict) else None
    try:
        if not owner_address or _raw_address(owner_address) != wallet_address:
            return None
        item_address = _raw_address(str(item.get("address") or ""))
    except Exception:
        return None
    if owner.get("is_scam") is True or item.get("trust") == "blacklist":
        return None

    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    collection = item.get("collection") if isinstance(item.get("collection"), dict) else {}
    index = item.get("index")
    raw_title = metadata.get("name")
    if not isinstance(raw_title, str) or not raw_title.strip():
        raw_title = collection.get("name")
    if not isinstance(raw_title, str) or not raw_title.strip():
        raw_title = f"TON NFT #{index}" if index is not None else "TON NFT"
    title = raw_title.strip()[:120]

    image_url = ""
    previews = item.get("previews")
    if isinstance(previews, list):
        candidates = []
        for preview in previews:
            if isinstance(preview, dict):
                url = _https_image(preview.get("url"))
                if url:
                    resolution = preview.get("resolution", "")
                    try:
                        width = int(str(resolution).split("x", 1)[0])
                    except (ValueError, IndexError):
                        width = 0
                    candidates.append((width, url))
        if candidates:
            image_url = max(candidates, key=lambda entry: entry[0])[1]
    if not image_url:
        image_url = _https_image(metadata.get("image"))

    return {
        "address": item_address,
        "wallet_address": wallet_address,
        "title": title,
        "image_url": image_url,
        "collection_address": str(collection.get("address") or "")[:128],
        "trust": str(item.get("trust") or "none"),
        "collection_verified": "true" if item.get("verified") is True else "false",
    }


async def fetch_wallet_nfts(address: str, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Fetch up to 500 directly owned NFTs; report whether pagination was complete."""
    wallet_address = _raw_address(address)
    token = os.getenv("TONAPI_TOKEN", "").strip()
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    owns_client = client is None
    session = client or httpx.AsyncClient(
        base_url=TONAPI_BASE_URL,
        timeout=httpx.Timeout(12.0, connect=5.0),
        follow_redirects=False,
    )
    items: list[dict[str, str]] = []
    complete = False
    try:
        for offset in range(0, MAX_ITEMS, PAGE_SIZE):
            # The unauthenticated TonAPI tier is limited to one request every four seconds.
            if offset and not token:
                await asyncio.sleep(4.1)
            response = await session.get(
                f"/v2/accounts/{wallet_address}/nfts",
                params={"limit": PAGE_SIZE, "offset": offset, "indirect_ownership": "false"},
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
            raw_items = body.get("nft_items") if isinstance(body, dict) else None
            if not isinstance(raw_items, list):
                raise ValueError("TON NFT provider returned an invalid response")
            page = [normal for raw in raw_items if (normal := _normalise_nft(raw, wallet_address))]
            items.extend(page)
            if len(raw_items) < PAGE_SIZE:
                complete = True
                break
        return {"items": items, "complete": complete, "wallet_address": wallet_address}
    finally:
        if owns_client:
            await session.aclose()
