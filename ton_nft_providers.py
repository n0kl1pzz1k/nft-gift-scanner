"""Independent live TON NFT state adapters (TonAPI + Toncenter v3)."""
from __future__ import annotations

import base64
import binascii
import os
from typing import Any
from urllib.parse import urlparse

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
    if payload[0] not in (0x11, 0x51, 0x91, 0xD1):
        raise ValueError("Unsupported TON friendly address flags")
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

    def __init__(self, *, api_key: str, base_url: str = "https://tonapi.io", timeout: float = 12.0):
        if not api_key:
            raise RuntimeError("TONAPI_KEY is required for the TonAPI NFT adapter")
        parsed = urlparse(base_url)
        if (parsed.scheme != "https" or parsed.hostname not in {"tonapi.io", "testnet.tonapi.io"}
                or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment):
            raise ValueError("TonAPI NFT adapter requires an approved HTTPS TON API hostname")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def get_nft_transfer(self, item_address: str, old_owner: str, new_owner: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                self.base_url + "/v2/accounts/" + new_owner + "/nfts/history",
                params={"limit": 1000},
                headers={"Authorization": "Bearer " + self.api_key},
            )
        response.raise_for_status()
        data = response.json()
        for operation in data.get("operations", []):
            item = operation.get("item") or {}
            old = operation.get("old_owner") or operation.get("old_owner_address") or {}
            new = operation.get("new_owner") or operation.get("new_owner_address") or item.get("owner") or {}
            old_address = old.get("address") if isinstance(old, dict) else old
            new_address = new.get("address") if isinstance(new, dict) else new
            candidate = item.get("address") or operation.get("nft_address")
            tx_hash = operation.get("transaction_hash") or operation.get("tx_hash")
            if not candidate or not tx_hash:
                continue
            try:
                matches = (
                    canonical_ton_address(candidate) == canonical_ton_address(item_address)
                    and canonical_ton_address(new_address or "") == canonical_ton_address(new_owner)
                    and (
                        not old_address
                        or canonical_ton_address(old_address) == canonical_ton_address(old_owner)
                    )
                )
            except ValueError:
                matches = False
            if matches and str(operation.get("operation", "")).lower() == "transfer":
                return {"tx_hash": str(tx_hash).removeprefix("0x").lower(), "event_index": 0}
        return None

    async def get_account_code_hash(self, account_address: str) -> str | None:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                self.base_url + "/v2/blockchain/accounts/" + account_address,
                headers={"Authorization": "Bearer " + self.api_key},
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        return str(data.get("code_hash") or "").lower() or None

    async def get_nft_state(self, item_address: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                self.base_url + "/v2/nfts/" + item_address,
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

    def __init__(self, *, api_key: str, base_url: str = "https://toncenter.com", timeout: float = 12.0):
        if not api_key:
            raise RuntimeError("TONCENTER_API_KEY is required for the Toncenter NFT adapter")
        parsed = urlparse(base_url)
        if (parsed.scheme != "https" or parsed.hostname not in {
                "toncenter.com", "testnet.toncenter.com"
        } or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment):
            raise ValueError("Toncenter NFT adapter requires an approved HTTPS TON API hostname")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def get_nft_transfer(self, item_address: str, old_owner: str, new_owner: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                self.base_url + "/api/v3/nft/transfers",
                params={"item_address": item_address, "limit": 100, "sort": "desc"},
                headers={"X-API-Key": self.api_key},
            )
        response.raise_for_status()
        data = response.json()
        for transfer in data.get("nft_transfers", []):
            if transfer.get("transaction_aborted") is not False:
                continue
            try:
                matches = (
                    canonical_ton_address(transfer.get("nft_address", "")) == canonical_ton_address(item_address)
                    and canonical_ton_address(transfer.get("old_owner", "")) == canonical_ton_address(old_owner)
                    and canonical_ton_address(transfer.get("new_owner", "")) == canonical_ton_address(new_owner)
                )
            except ValueError:
                matches = False
            tx_hash = transfer.get("transaction_hash")
            if matches and tx_hash:
                return {
                    "tx_hash": str(tx_hash).removeprefix("0x").lower(),
                    "event_index": 0,
                }
        return None

    async def get_account_code_hash(self, account_address: str) -> str | None:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                self.base_url + "/api/v3/accountStates",
                params={"address": account_address, "include_boc": "false"},
                headers={"X-API-Key": self.api_key},
            )
        response.raise_for_status()
        data = response.json()
        accounts = data.get("accounts") or []
        if len(accounts) != 1:
            return None
        return str(accounts[0].get("code_hash") or "").lower() or None

    async def get_nft_state(self, item_address: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                self.base_url + "/api/v3/nft/items",
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
def configured_testnet_nft_providers() -> list[Any]:
    """Use the testnet API hosts and separately scoped credentials."""
    configured = {
        item.strip() for item in os.getenv("TON_TESTNET_NFT_PROVIDER_IDS", "").split(",") if item.strip()
    }
    if configured != {"tonapi", "toncenter"}:
        raise RuntimeError("TON_TESTNET_NFT_PROVIDER_IDS must explicitly be 'tonapi,toncenter'")
    return [
        TonApiNftProvider(
            api_key=os.getenv("TON_TESTNET_TONAPI_KEY", ""),
            base_url="https://testnet.tonapi.io",
        ),
        ToncenterNftProvider(
            api_key=os.getenv("TON_TESTNET_TONCENTER_API_KEY", ""),
            base_url="https://testnet.toncenter.com",
        ),
    ]
