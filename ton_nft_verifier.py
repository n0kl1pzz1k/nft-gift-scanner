"""Dual-provider TEP-62 NFT ownership verification for seller inventory."""
from __future__ import annotations

import os
from typing import Any, Protocol

from financial_ledger import _hash
from ton_nft_providers import canonical_ton_address


class TonNftStateProvider(Protocol):
    provider_id: str

    async def get_nft_state(self, item_address: str) -> dict[str, Any] | None:
        """Return live TEP-62 get_nft_data plus the resolved collection item identity."""


def _configured_providers() -> set[str]:
    values = {part.strip() for part in os.getenv("TON_NFT_PROVIDER_IDS", "").split(",") if part.strip()}
    if values != {"tonapi", "toncenter"}:
        raise RuntimeError("TON_NFT_PROVIDER_IDS must explicitly be 'tonapi,toncenter'")
    return values


def _validate_seller_nft_request(
    *,
    seller_user_id: str,
    wallet_user_id: str,
    verified_wallet_address: str,
    asset_type: str,
    network: str,
    collection_address: str,
    token_id: str,
    item_address: str,
    allowed_collections: dict[str, set[str]],
    providers: list[TonNftStateProvider],
) -> dict[str, Any]:
    """Verify item, collection, index and owner using two independent chain providers.

    The verified wallet binding must come from the server-side TON Proof service.
    TON Proof alone is not treated as proof of NFT ownership.
    """
    supported = {"ton_nft", "collectible_username", "collectible_phone_number"}
    if asset_type not in supported or network != "ton-mainnet":
        raise ValueError("Only supported TON mainnet NFT asset types can be verified")
    if str(seller_user_id) != str(wallet_user_id):
        raise PermissionError("The verified wallet is not bound to this seller")
    if not verified_wallet_address or not collection_address or not item_address:
        raise ValueError("A verified seller wallet and canonical NFT identity are required")
    verified_wallet_address = canonical_ton_address(verified_wallet_address)
    collection_address = canonical_ton_address(collection_address)
    item_address = canonical_ton_address(item_address)
    try:
        canonical_index = str(int(token_id))
    except (TypeError, ValueError):
        raise ValueError("NFT token ID must be a non-negative integer") from None
    if canonical_index != str(token_id).strip() or int(canonical_index) < 0:
        raise ValueError("NFT token ID is not canonical")
    approved = {canonical_ton_address(value) for value in allowed_collections.get(asset_type, set())}
    if collection_address not in approved:
        raise PermissionError("NFT collection is not in the reviewed collection registry")

    trusted = _configured_providers()
    by_id = {provider.provider_id: provider for provider in providers}
    if len(by_id) < 2 or not set(by_id).issubset(trusted):
        raise PermissionError("Two configured independent TON NFT providers are required")
    return {
        "status": "verification_required",
        "reason": "Use verify_seller_nft_ownership_async with live provider responses",
        "asset_type": asset_type,
        "network": network,
        "collection_address": collection_address,
        "token_id": canonical_index,
        "item_address": item_address,
        "owner_wallet_address": verified_wallet_address,
    }


async def verify_seller_nft_ownership_async(
    *,
    seller_user_id: str,
    wallet_user_id: str,
    verified_wallet_address: str,
    asset_type: str,
    network: str,
    collection_address: str,
    token_id: str,
    item_address: str,
    allowed_collections: dict[str, set[str]],
    providers: list[TonNftStateProvider],
) -> dict[str, Any]:
    """Return immutable-ready evidence only if two independent providers agree."""
    base = _validate_seller_nft_request(
        seller_user_id=seller_user_id, wallet_user_id=wallet_user_id,
        verified_wallet_address=verified_wallet_address, asset_type=asset_type,
        network=network, collection_address=collection_address, token_id=token_id,
        item_address=item_address, allowed_collections=allowed_collections,
        providers=providers,
    )
    states = []
    for provider in providers:
        state = await provider.get_nft_state(item_address)
        if state is None:
            raise ValueError("NFT item was not found by every configured provider")
        normalized = {
            "item_address": canonical_ton_address(str(state.get("item_address") or "")),
            "collection_address": canonical_ton_address(str(state.get("collection_address") or "")),
            "token_id": str(state.get("token_id") or ""),
            "owner_address": canonical_ton_address(str(state.get("owner_address") or "")) if state.get("owner_address") else "",
            "initialized": state.get("initialized") is True,
        }
        states.append((provider.provider_id, normalized))
    evidence_hashes = {_hash(state) for _, state in states}
    if len(evidence_hashes) != 1:
        raise ValueError("TON providers disagree about NFT ownership; listing is blocked")
    state = states[0][1]
    if state["initialized"] is not True:
        raise ValueError("NFT item is not initialized")
    if state["item_address"] != base["item_address"]:
        raise ValueError("NFT item address mismatch")
    if state["collection_address"] != base["collection_address"]:
        raise ValueError("NFT collection mismatch")
    if state["token_id"] != base["token_id"]:
        raise ValueError("NFT token ID mismatch")
    if state["owner_address"] != verified_wallet_address:
        raise PermissionError("Seller wallet is not the current on-chain NFT owner")
    return {
        "verified": True,
        "asset_type": asset_type,
        "network": network,
        "collection_address": collection_address,
        "token_id": base["token_id"],
        "item_address": item_address,
        "owner_wallet_address": state["owner_address"],
        "provider_ids": sorted(provider_id for provider_id, _ in states),
        "evidence_hash": next(iter(evidence_hashes)),
    }
