"""Fail-closed asset policy for the TON-only GIFT RADAR marketplace.

Telegram Stars are the only in-Mini-App checkout currency. TON is the only
supported blockchain network and TON Connect is the only wallet protocol.
No Jetton, stablecoin, TRON or alternative-chain payment assets are allowlisted.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Chain(str, Enum):
    TON = "ton-mainnet"


class Surface(str, Enum):
    TELEGRAM_MINI_APP = "telegram-mini-app"


@dataclass(frozen=True)
class Asset:
    asset_id: str
    chain: Chain
    decimals: int
    display_symbol: str
    asset_kind: str = "native"


ASSETS: dict[str, Asset] = {
    "TON": Asset(
        asset_id="TON",
        chain=Chain.TON,
        decimals=9,
        display_symbol="TON",
        asset_kind="native",
    ),
}


class AssetRejected(ValueError):
    """Raised when an asset does not match the TON-only product policy."""


def get_asset(asset_id: str, chain: str, master_address: str = "", surface: str = "telegram-mini-app") -> Asset:
    try:
        selected_surface = Surface(surface)
        selected_chain = Chain(chain)
    except ValueError as exc:
        raise AssetRejected("Only TON network interactions are supported") from exc
    asset = ASSETS.get(asset_id)
    if asset is None or selected_chain is not asset.chain:
        raise AssetRejected("Only native TON is supported; Jettons and other networks are rejected")
    if master_address not in ("", "native"):
        raise AssetRejected("Native TON must not be represented by a token master contract")
    return asset


def validate_payment_amount(asset_id: str, amount_raw: int) -> int:
    if asset_id != "TON":
        raise AssetRejected("Only native TON is supported")
    try:
        amount = int(amount_raw)
    except (TypeError, ValueError):
        raise AssetRejected("Amount must be an integer in nanoTON") from None
    if amount < 1:
        raise AssetRejected("Amount must be positive")
    if amount > 9_000_000_000_000_000:
        raise AssetRejected("Amount exceeds the supported maximum")
    return amount
