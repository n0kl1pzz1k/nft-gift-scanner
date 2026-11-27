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

# Official Fragment NFT collections. Addresses are stored in raw form to avoid
# friendly/raw representation mismatches during classification.
FRAGMENT_COLLECTIONS = {
    "0:80d78a35f955a14b679faa887ff4cd5bfc0f43b4a4eea2a7e6927f3701b273c2": "username",
    "0:0e41dc1dc3c9067ed24248580e12b3359818d83dee0304fabcf80845eafafdb2": "number",
}


def build_nft_transfer_payload(
    *,
    new_owner_address: str,
    response_destination: str,
    query_id: int,
) -> str:
    """Build the standard TON NFT transfer body (TEP-62) for wallet signing."""
    import base64
    from tonsdk.boc import Cell
    from tonsdk.utils import Address

    recipient = Address(new_owner_address)
    response = Address(response_destination)
    if int(query_id) < 0 or int(query_id) >= 2**64:
        raise ValueError("TON NFT transfer query ID must fit uint64")
    body = Cell()
    body.bits.write_uint(0x5FCC3D14, 32)
    body.bits.write_uint(int(query_id), 64)
    body.bits.write_address(recipient)
    body.bits.write_address(response)
    body.bits.write_bit(0)  # custom_payload: none
    body.bits.write_coins(1)  # forward_amount: 1 nanoton
    body.bits.write_bit(0)  # forward_payload: empty
    return base64.b64encode(body.to_boc(False)).decode("ascii")


def normalise_ton_address(value: str) -> str:
    """Return a canonical raw TON address for trusted on-chain comparisons."""
    return _raw_address(value)


def fragment_asset_type(collection_address: str) -> str | None:
    try:
        return FRAGMENT_COLLECTIONS.get(_raw_address(collection_address))
    except Exception:
        return None


def fragment_collection_address(asset_type: str) -> str | None:
    for address, kind in FRAGMENT_COLLECTIONS.items():
        if kind == str(asset_type):
            return address
    return None


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


def _normalise_nft(item: dict[str, Any], wallet_address: str) -> dict[str, Any] | None:
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

    raw_collection_address = ""
    if collection.get("address"):
        try:
            raw_collection_address = _raw_address(str(collection["address"]))
        except Exception:
            raw_collection_address = ""

    return {
        "address": item_address,
        "wallet_address": wallet_address,
        "title": title,
        "image_url": image_url,
        "attributes": metadata.get("attributes") if isinstance(metadata.get("attributes"), (list, dict)) else [],
        "collection_address": raw_collection_address,
        "trust": str(item.get("trust") or "none"),
        "collection_verified": "true" if item.get("verified") is True else "false",
    }


async def fetch_nft_owner(item_address: str, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Re-read the current on-chain owner of one NFT immediately before listing."""
    address = _raw_address(item_address)
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
    try:
        # Keep the free public tier below its one-request-per-four-seconds limit.
        if not token:
            await asyncio.sleep(4.1)
        response = await session.get(f"/v2/nfts/{address}", headers=headers)
        if response.status_code == 404:
            return {"owner_address": None, "is_scam": False, "trust": "none"}
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("TON NFT provider returned an invalid item response")
        owner = body.get("owner") if isinstance(body.get("owner"), dict) else {}
        owner_address = owner.get("address")
        try:
            normalized_owner = _raw_address(owner_address) if owner_address else None
        except Exception:
            normalized_owner = None
        return {
            "owner_address": normalized_owner,
            "is_scam": owner.get("is_scam") is True,
            "trust": str(body.get("trust") or "none"),
        }
    finally:
        if owns_client:
            await session.aclose()


async def fetch_wallet_nft_history(
    address: str,
    *,
    limit: int = 1000,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent NFT transfer operations for a wallet from TonAPI."""
    wallet_address = _raw_address(address)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("TON NFT history limit must be between 1 and 1000")
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
    try:
        if not token:
            await asyncio.sleep(4.1)
        response = await session.get(
            f"/v2/accounts/{wallet_address}/nfts/history",
            params={"limit": limit},
            headers=headers,
        )
        response.raise_for_status()
        body = response.json()
        operations = body.get("operations") if isinstance(body, dict) else None
        if not isinstance(operations, list) or any(not isinstance(item, dict) for item in operations):
            raise ValueError("TON NFT history provider returned an invalid response")
        return operations
    finally:
        if owns_client:
            await session.aclose()


def find_confirmed_nft_transfer(
    operations: list[dict[str, Any]],
    *,
    item_address: str,
    source_address: str,
    destination_address: str,
    not_before: int,
) -> dict[str, Any] | None:
    """Find an indexed transfer matching the exact NFT, wallets and delivery window."""
    item_raw = _raw_address(item_address)
    source_raw = _raw_address(source_address)
    destination_raw = _raw_address(destination_address)
    cutoff = int(not_before)
    for operation in operations:
        if not isinstance(operation, dict) or operation.get("operation") != "transfer":
            continue
        item = operation.get("item") if isinstance(operation.get("item"), dict) else {}
        source = operation.get("source") if isinstance(operation.get("source"), dict) else {}
        destination = operation.get("destination") if isinstance(operation.get("destination"), dict) else {}
        try:
            if _raw_address(str(item.get("address") or "")) != item_raw:
                continue
            if _raw_address(str(source.get("address") or "")) != source_raw:
                continue
            if _raw_address(str(destination.get("address") or "")) != destination_raw:
                continue
        except Exception:
            continue
        timestamp = operation.get("utime")
        transaction_hash = str(operation.get("transaction_hash") or "").strip()
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < cutoff:
            continue
        if not transaction_hash or len(transaction_hash) > 256:
            continue
        return {
            "transaction_hash": transaction_hash,
            "utime": timestamp,
            "lt": operation.get("lt"),
        }
    return None


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
