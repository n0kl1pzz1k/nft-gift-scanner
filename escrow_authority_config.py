"""Platform-only authority configuration for the TON testnet escrow vertical slice.

No caller-provided seller/buyer payload may set signer, arbitrator or resolver members.
Addresses are raw TON addresses so the selected network is explicit and unambiguous.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
from typing import Mapping


_RAW_TON_ADDRESS = re.compile(r"^0:([0-9a-fA-F]{64})$")
_FORBIDDEN_CLIENT_KEYS = frozenset({
    "signers", "signer_addresses", "signer1", "signer2", "signer3",
    "arbitrators", "arbitrator_addresses", "arbitrator1", "arbitrator2", "arbitrator3",
    "resolver_multisig", "resolver_address", "platform_config_authority",
    "authority_config", "authority_config_hash",
})


def canonical_testnet_address(value: str, field: str) -> str:
    raw = str(value or "").strip()
    match = _RAW_TON_ADDRESS.fullmatch(raw)
    if not match:
        raise ValueError(f"{field} must be a raw TON basechain address (0:<64 hex chars>)")
    return "0:" + match.group(1).lower()


@dataclass(frozen=True)
class PlatformEscrowAuthorityConfig:
    network: str
    platform_config_authority: str
    signers: tuple[str, str, str]
    signer_threshold: int
    arbitrators: tuple[str, str, str]
    arbitrator_threshold: int
    resolver_multisig: str
    resolver_code_hash: str
    test_jetton_master: str | None
    version: str


def load_platform_escrow_authorities(environ: Mapping[str, str] | None = None) -> PlatformEscrowAuthorityConfig:
    env = os.environ if environ is None else environ
    network = env.get("GIFT_RADAR_ESCROW_NETWORK", "").strip()
    if network != "ton-testnet":
        raise ValueError("Escrow authority configuration is disabled unless GIFT_RADAR_ESCROW_NETWORK=ton-testnet")

    platform = canonical_testnet_address(env.get("GIFT_RADAR_ESCROW_PLATFORM_CONFIG_AUTHORITY", ""), "platform_config_authority")
    signers = tuple(canonical_testnet_address(env.get(f"GIFT_RADAR_ESCROW_SIGNER_{i}", ""), f"signer_{i}") for i in range(1, 4))
    arbitrators = tuple(canonical_testnet_address(env.get(f"GIFT_RADAR_ESCROW_ARBITRATOR_{i}", ""), f"arbitrator_{i}") for i in range(1, 4))
    resolver = canonical_testnet_address(env.get("GIFT_RADAR_ESCROW_RESOLVER_MULTISIG", ""), "resolver_multisig")
    resolver_hash = env.get("GIFT_RADAR_ESCROW_RESOLVER_CODE_HASH", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", resolver_hash):
        raise ValueError("A pinned resolver multisig code hash is required")

    all_authorities = (platform, *signers, *arbitrators, resolver)
    if len(set(all_authorities)) != len(all_authorities):
        raise ValueError("Platform authority, signers, arbitrators and resolver must all be distinct")

    jetton_master_raw = env.get("GIFT_RADAR_TEST_JETTON_MASTER", "").strip()
    jetton_master = canonical_testnet_address(jetton_master_raw, "test_jetton_master") if jetton_master_raw else None

    return PlatformEscrowAuthorityConfig(
        network=network,
        platform_config_authority=platform,
        signers=signers,
        signer_threshold=2,
        arbitrators=arbitrators,
        arbitrator_threshold=2,
        resolver_multisig=resolver,
        resolver_code_hash=resolver_hash,
        test_jetton_master=jetton_master,
        version=env.get("GIFT_RADAR_ESCROW_AUTHORITY_VERSION", "1").strip() or "1",
    )


def build_testnet_escrow_snapshot(
    *,
    seller_address: str,
    buyer_address: str,
    nft_item_address: str,
    asset_kind: str,
    amount_raw: int,
    client_payload: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    payload = client_payload or {}
    injected = sorted(_FORBIDDEN_CLIENT_KEYS.intersection(payload.keys()))
    if injected:
        raise ValueError("Client payload cannot set platform authority fields")

    authorities = load_platform_escrow_authorities(environ)
    seller = canonical_testnet_address(seller_address, "seller_address")
    buyer = canonical_testnet_address(buyer_address, "buyer_address")
    nft_item = canonical_testnet_address(nft_item_address, "nft_item_address")
    if seller == buyer:
        raise ValueError("Seller and buyer must be different addresses")
    if seller in authorities.signers or seller in authorities.arbitrators or seller in {authorities.platform_config_authority, authorities.resolver_multisig}:
        raise ValueError("Seller cannot be a platform authority, signer, arbitrator or resolver")
    if buyer in authorities.signers or buyer in authorities.arbitrators or buyer in {authorities.platform_config_authority, authorities.resolver_multisig}:
        raise ValueError("Buyer cannot be a platform authority, signer, arbitrator or resolver")
    if asset_kind not in {"TON", "TEST_USDT_JETTON"}:
        raise ValueError("Only TON and the platform-issued test Jetton are supported in this testnet flow")
    if not isinstance(amount_raw, int) or isinstance(amount_raw, bool) or amount_raw <= 0:
        raise ValueError("amount_raw must be a positive integer")
    if asset_kind == "TEST_USDT_JETTON" and not authorities.test_jetton_master:
        raise ValueError("Platform test Jetton master is not configured")

    snapshot: dict[str, object] = {
        "schema_version": 1,
        "network": authorities.network,
        "authority_version": authorities.version,
        "platform_config_authority": authorities.platform_config_authority,
        "signers": list(authorities.signers),
        "signer_threshold": authorities.signer_threshold,
        "arbitrators": list(authorities.arbitrators),
        "arbitrator_threshold": authorities.arbitrator_threshold,
        "resolver_multisig": authorities.resolver_multisig,
        "resolver_code_hash": authorities.resolver_code_hash,
        "seller": seller,
        "buyer": buyer,
        "nft_item": nft_item,
        "asset_kind": asset_kind,
        "amount_raw": amount_raw,
        "test_jetton_master": authorities.test_jetton_master if asset_kind == "TEST_USDT_JETTON" else None,
        "max_lifetime_seconds": 30 * 24 * 60 * 60,
        "payment_wait_seconds": 24 * 60 * 60,
    }
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    snapshot["configuration_hash"] = hashlib.sha256(canonical).hexdigest()
    return snapshot
