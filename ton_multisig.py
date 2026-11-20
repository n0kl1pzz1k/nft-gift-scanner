"""Read-only verification of a TON treasury's on-chain multisig configuration."""
from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

TONAPI_BASE_URL = "https://tonapi.io"


def _raw_address(value: str) -> str:
    from tonsdk.utils import Address

    address = Address(value)
    return f"{int(address.wc)}:{bytes(address.hash_part).hex()}"


def is_two_of_three_multisig(config: Any, expected_address: str) -> bool:
    """Accept only the exact treasury address with 2-of-3 distinct signers."""
    if not isinstance(config, dict):
        return False
    try:
        expected = _raw_address(expected_address)
        actual = _raw_address(str(config.get("address") or ""))
        threshold = config.get("threshold")
        signers = config.get("signers")
        if actual != expected or isinstance(threshold, bool) or threshold != 2:
            return False
        if not isinstance(signers, list) or len(signers) != 3:
            return False
        normalised = [_raw_address(str(signer)) for signer in signers]
        return len(set(normalised)) == 3
    except Exception:
        return False


async def fetch_ton_multisig_config(
    address: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch TonAPI's indexed multisig account configuration for an address."""
    raw_address = _raw_address(address)
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
        response = await session.get(f"/v2/multisig/{raw_address}", headers=headers)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("TONAPI returned an invalid multisig response")
        return payload
    finally:
        if owns_client:
            await session.aclose()
