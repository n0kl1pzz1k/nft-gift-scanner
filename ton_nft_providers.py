"""Independent live TON NFT state adapters (TonAPI + Toncenter v3)."""
from __future__ import annotations

import base64
import binascii
import os
from typing import Any

import httpx


def canonical_ton_address(value: str) -> str:
    """Normalize raw or friendly TON address to workchain:256-bit-hex."""
    raw = str(value or "").strip()
    if ":" in raw:
        parts = raw.split(":")
        if len(parts) != 2 or len(parts[1]) != 64:
            raise ValueError("Malformed raw TON address")
        workchain = int(parts[0])
        account = bytes.fromhex(parts[1])
        if not -128 <= workchain <= 127:
            raise ValueError("Unsupported TON workchain")
        return f"{workchain}:{account.hex()}"
    padded = raw.replace("-", "+").replace("_", "/")
    padded += "=" * ((4 - len(padded) % 4) % 4)
    try:
        decoded = base64.b64decode(padded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Malformed friendly TON address") from None
    if len(decoded) != 36:
        raise ValueError("Friendly TON address must decode to 36 bytes")
    payload, checksum = decoded[:34], decoded[34:]
    crc = 0
    for byte in payload:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    if checksum != crc.to_bytes(2, "big"):
        raise ValueError("Friendly TON address checksum mismatch")
    workchain = int.from_bytes(payload[1:2], "big", signed=True)
    return f"{workchain}:{payload[2:34].hex()}"


class TonApiNftProvider:
    provider_id = "tonapi"

    def __init__(self, *, api_key: str, timeout: float = 12.0):
        if not api_key:
            raise RuntimeError("TONAPI_KEY is required for the TonAPI NFT adapter")
        self.api_key = api_key
        self.timeout = timeout

    async def get_nft_state(self, item_address: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                "https://tonapi.io/v2/nfts/" + item_address,
                headers={"Authorization": "Bearer " + self.api_key},
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        collection = data.get("collection") or {}
        owner = data.get("owner")
        return {
            "item_address": canonical_ton_address(data.get("address", "")),
            "collection_address": canonical_ton_address(collection.get("address", "")),
            "token_id": str(data.get("index", "")),
            "owner_address": canonical_ton_address(owner.get("address", "")) if owner else "",
            "initialized": bool(owner and data.get("address")),
        }


class ToncenterNftProvider:
    provider_id = "toncenter"

    def __init__(self, *, api_key: str, timeout: float = 12.0):
        if not api_key:
            raise RuntimeError("TONCENTER_API_KEY is required for the Toncenter NFT adapter")
        self.api_key = api_key
        self.timeout = timeout

    async def get_nft_state(self, item_address: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                "https://toncenter.com/api/v3/nft/items",
                params={"address": item_address, "limit": 1},
                headers={"X-API-Key": self.api_key},
            )
        response.raise_for_status()
        data = response.json()
        items = data.get("nft_items") or data.get("items") or []
        if not items:
            return None
        item = items[0]
        owner = item.get("owner_address")
        collection = item.get("collection_address")
        address = item.get("address")
        initialized = bool(item.get("init", item.get("initialized", owner is not None)))
        return {
            "item_address": canonical_ton_address(address or ""),
            "collection_address": canonical_ton_address(collection or ""),
            "token_id": str(item.get("index", "")),
            "owner_address": canonical_ton_address(owner) if owner else "",
            "initialized": initialized,
        }


def configured_nft_providers() -> list[Any]:
    """Require both named independent operators; no alias-based quorum."""
    configured = {
        item.strip() for item in os.getenv("TON_NFT_PROVIDER_IDS", "").split(",") if item.strip()
    }
    if configured != {"tonapi", "toncenter"}:
        raise RuntimeError("TON_NFT_PROVIDER_IDS must explicitly be 'tonapi,toncenter'")
    return [
        TonApiNftProvider(api_key=os.getenv("TONAPI_KEY", "")),
        ToncenterNftProvider(api_key=os.getenv("TONCENTER_API_KEY", "")),
    ]
