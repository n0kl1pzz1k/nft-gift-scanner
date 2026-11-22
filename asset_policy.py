"""Fail-closed asset identity policy for GIFT RADAR.

This module validates asset identifiers only. It does not query chains, verify
transactions, connect wallets, sign, broadcast, or settle payments.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Chain(str, Enum):
    TON = "ton-mainnet"
    TRON = "tron-mainnet"


class Surface(str, Enum):
    TELEGRAM_MINI_APP = "telegram-mini-app"
    EXTERNAL = "external"


@dataclass(frozen=True)
class Asset:
    asset_id: str
    chain: Chain
    master_address: str
    decimals: int
    display_symbol: str
    wallet_connect_allowed_in_mini_app: bool
    payment_enabled: bool = False


# Addresses are identifiers, not secrets. These values are sourced from the
# issuer's official supported-protocol documentation and must be re-reviewed
# before any payment feature is enabled.
ASSETS: dict[str, Asset] = {
    "usdt-ton": Asset(
        asset_id="usdt-ton",
        chain=Chain.TON,
        master_address="EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs",
        decimals=6,
        display_symbol="USDT",
        wallet_connect_allowed_in_mini_app=True,
        payment_enabled=False,
    ),
    "usdt-tron": Asset(
        asset_id="usdt-tron",
        chain=Chain.TRON,
        master_address="TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
        decimals=6,
        display_symbol="USDT",
        wallet_connect_allowed_in_mini_app=False,
        payment_enabled=False,
    ),
    # GRAM deliberately omitted: the project has not approved a canonical
    # master address, decimals, issuer provenance, or risk review.
}


class AssetRejected(ValueError):
    """Raised when an asset does not match the reviewed policy."""


def get_asset(asset_id: str, chain: str, master_address: str, surface: str) -> Asset:
    """Return a reviewed asset only on an exact chain + master-address match.

    Never use token symbol, name, logo, decimals, or metadata to identify an
    asset. Unknown or lookalike contracts fail closed.
    """
    try:
        selected_surface = Surface(surface)
        selected_chain = Chain(chain)
    except ValueError as exc:
        raise AssetRejected("unsupported surface or network") from exc

    asset = ASSETS.get(asset_id)
    if asset is None:
        raise AssetRejected("asset is not allowlisted")

    if asset.chain is not selected_chain:
        raise AssetRejected("asset network mismatch")

    if asset.master_address != master_address:
        raise AssetRejected("unrecognized token master/contract address")

    if selected_surface is Surface.TELEGRAM_MINI_APP:
        if not asset.wallet_connect_allowed_in_mini_app or selected_chain is not Chain.TON:
            raise AssetRejected("non-TON asset interaction is not allowed in Telegram Mini Apps")

    return asset


def is_crypto_payment_enabled(asset_id: str) -> bool:
    """Crypto checkout stays disabled until separate compliance/security approval."""
    asset = ASSETS.get(asset_id)
    return bool(asset and asset.payment_enabled)
