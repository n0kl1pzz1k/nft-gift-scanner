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
    asset_kind: str = "jetton"


# Addresses are identifiers, not secrets. These values are sourced from the
# issuer's official supported-protocol documentation and must be re-reviewed
# before any payment feature is enabled.
ASSETS: dict[str, Asset] = {
    # GRAM is the native TON coin. It has no Jetton master contract.
    "GRAM": Asset(
        asset_id="GRAM", chain=Chain.TON, master_address="", decimals=9,
        display_symbol="GRAM", wallet_connect_allowed_in_mini_app=True,
        payment_enabled=True, asset_kind="native",
    ),
    "usdt-ton": Asset(
        asset_id="usdt-ton",
        chain=Chain.TON,
        master_address="EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs",
        decimals=6,
        display_symbol="USDT",
        wallet_connect_allowed_in_mini_app=True,
        payment_enabled=True,
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
    # A Jetton that merely displays GRAM/Gram is not the native GRAM asset.
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

    if asset.asset_kind == "native":
        if master_address not in ("", "native"):
            raise AssetRejected("native GRAM must not be represented by a Jetton master contract")
    elif asset.master_address != master_address:
        raise AssetRejected("unrecognized token master/contract address")

    if (
        selected_surface is Surface.TELEGRAM_MINI_APP
        and (not asset.wallet_connect_allowed_in_mini_app or selected_chain is not Chain.TON)
    ):
        raise AssetRejected("non-TON asset interaction is not allowed in Telegram Mini Apps")

    return asset



# Conservative anti-dust floors in the smallest on-chain units. These are
# policy guardrails, not exchange-rate conversions or price recommendations.
MIN_PAYMENT_RAW: dict[str, int] = {
    "GRAM": 10_000_000,       # 0.01 native TON
    "usdt-ton": 100_000,       # 0.10 USDT (6 decimals)
}


def validate_payment_amount(asset_id: str, amount_raw: int) -> int:
    asset = ASSETS.get(asset_id)
    if asset is None:
        raise AssetRejected("Unsupported payment asset")
    try:
        amount = int(amount_raw)
    except (TypeError, ValueError):
        raise AssetRejected("Payment amount must be an integer in base units") from None
    minimum = MIN_PAYMENT_RAW.get(asset_id, 1)
    if amount < minimum:
        raise AssetRejected("Payment amount is below the minimum anti-dust threshold")
    if amount > 9_000_000_000_000_000:
        raise AssetRejected("Payment amount exceeds the supported maximum")
    return amount


def is_crypto_payment_enabled(asset_id: str) -> bool:
    """Return true only after a reviewed TON testnet adapter exists and all gates pass."""
    from release_policy import TON_TESTNET_DEPOSIT_ADAPTER_READY

    asset = ASSETS.get(asset_id)
    # Every currently allowlisted TON asset and provider adapter targets mainnet.
    # Do not let environment flags turn that implementation into a real-money rail.
    if (
        not TON_TESTNET_DEPOSIT_ADAPTER_READY
        or asset is None
        or not asset.payment_enabled
        or asset.chain.value != "ton-testnet"
    ):
        return False
    if asset_id not in {"GRAM", "usdt-ton"}:
        return False

    import os

    database_url = os.getenv("DATABASE_URL", "").strip()
    postgres_url = database_url.startswith((
        "postgres://", "postgresql://", "postgresql+psycopg://"
    ))
    provider_ids = {
        part.strip() for part in os.getenv("TON_DEPOSIT_PROVIDER_IDS", "").split(",")
        if part.strip()
    }
    gates = (
        os.getenv("TON_CRYPTO_DEPOSITS_ENABLED", "false").strip().lower() == "true",
        os.getenv("TON_DEPOSIT_MONITOR_ENABLED", "false").strip().lower() == "true",
        os.getenv("MARKETPLACE_REAL_DEALS_ENABLED", "false").strip().lower() == "true",
        os.getenv("GIFT_RADAR_STORAGE_BACKEND", "").strip().lower() == "postgres",
        postgres_url,
        provider_ids == {"tonapi", "toncenter"},
        bool(os.getenv("TONCENTER_API_KEY", "").strip()),
        os.getenv("TONAPI_EMBED_URL", "").strip().startswith("https://"),
        bool(os.getenv("TON_DEPOSIT_ADDRESS", "").strip()),
        os.getenv("KZ_LEGAL_REVIEW_APPROVED", "false").strip().lower() == "true",
        os.getenv("KZ_DATA_RESIDENCY_VERIFIED", "false").strip().lower() == "true",
        os.getenv("KZ_TAX_REVIEW_APPROVED", "false").strip().lower() == "true",
        os.getenv("KZ_SECURITY_REVIEW_APPROVED", "false").strip().lower() == "true",
    )
    return all(gates)
