"""Read-only verification of a TON Core multisig-contract-v2 treasury."""
from __future__ import annotations

import asyncio
import base64
import math
import os
from typing import Any

import httpx

TONAPI_BASE_URL = "https://tonapi.io"
EXPECTED_TON_MULTISIG_SIGNERS = [
    "0:372b63c65f2a2d81af7d7a7d995c4fcd7bdc18e6efa50b4ee840ae9b45dc6d10",
    "0:eacaac33e932a8b67ae31e712d5a7969660130363e48e789f96c03a6162bc147",
    "0:8ede0858321e76adb84ee65790431bb800050acfc0ee5b6c1255e65137f3221c",
]


def _raw_address(value: str) -> str:
    from tonsdk.utils import Address

    address = Address(value)
    return f"{int(address.wc)}:{bytes(address.hash_part).hex()}"


def _parse_stack_number(item: Any) -> int:
    if not isinstance(item, dict) or item.get("type") != "num":
        raise ValueError("TONAPI multisig getter returned a non-numeric stack item")
    value = item.get("num")
    if not isinstance(value, str):
        raise ValueError("TONAPI multisig getter returned an invalid numeric value")
    try:
        return int(value, 16) if value.lower().startswith("0x") else int(value, 10)
    except ValueError as exc:
        raise ValueError("TONAPI multisig getter returned an invalid numeric value") from exc


def _read_hashmap_addresses(boc_base64: str, *, key_bits: int = 8) -> list[str]:
    """Decode the contract's Hashmap(key_bits, MsgAddressInt) root cell."""
    from tonsdk.boc import Cell
    from tonsdk.utils import Address

    if not isinstance(boc_base64, str) or not boc_base64:
        raise ValueError("TONAPI returned an empty multisig dictionary cell")
    try:
        root = Cell.one_from_boc(base64.b64decode(boc_base64, validate=True))
    except Exception as exc:
        raise ValueError("TONAPI returned an invalid dictionary BOC") from exc

    def read_label(slice_, remaining: int) -> list[int]:
        first = slice_.read_bit()
        if first == 0:
            length = 0
            while slice_.read_bit() == 1:
                length += 1
            if length > remaining:
                raise ValueError("Invalid TON dictionary label length")
            return list(slice_.read_bits(length))
        second = slice_.read_bit()
        length_width = math.ceil(math.log2(remaining + 1))
        if second == 0:
            length = slice_.read_uint(length_width) if length_width else 0
            if length > remaining:
                raise ValueError("Invalid TON dictionary label length")
            return list(slice_.read_bits(length))
        repeated_bit = slice_.read_bit()
        length = slice_.read_uint(length_width) if length_width else 0
        if length > remaining:
            raise ValueError("Invalid TON dictionary label length")
        return [repeated_bit] * length

    entries: list[tuple[int, str]] = []

    def walk(cell, remaining: int, prefix: list[int]) -> None:
        slice_ = cell.begin_parse()
        label = read_label(slice_, remaining)
        prefix = prefix + label
        remaining -= len(label)
        if remaining == 0:
            # The contract stores standard internal addresses without anycast.
            if slice_.read_uint(2) != 2 or slice_.read_bit() != 0:
                raise ValueError("Multisig signer is not a standard non-anycast address")
            workchain = slice_.read_int(8)
            hash_part = slice_.read_bytes(32).hex()
            slice_.end_parse()
            key = int("".join(str(bit) for bit in prefix), 2) if prefix else 0
            entries.append((key, f"{workchain}:{hash_part}"))
            return
        if slice_.bits or slice_.ref_offset:
            raise ValueError("Malformed TON dictionary fork node")
        if len(cell.refs) != 2:
            raise ValueError("Malformed TON dictionary fork references")
        walk(cell.refs[0], remaining - 1, prefix + [0])
        walk(cell.refs[1], remaining - 1, prefix + [1])

    walk(root, key_bits, [])
    entries.sort(key=lambda item: item[0])
    keys = [key for key, _ in entries]
    if keys != list(range(len(keys))):
        raise ValueError("TON multisig signer dictionary has non-sequential indexes")
    return [address for _, address in entries]


def decode_multisig_v2_getter(payload: Any, expected_address: str) -> dict[str, Any]:
    """Decode TON Core multisig-v2 get_multisig_data response from TONAPI."""
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("TONAPI could not execute get_multisig_data")
    if payload.get("exit_code") != 0:
        raise ValueError("TON multisig get_multisig_data getter failed")
    stack = payload.get("stack")
    if not isinstance(stack, list) or len(stack) != 4:
        raise ValueError("TONAPI returned an unexpected multisig getter stack")
    next_order_seqno = _parse_stack_number(stack[0])
    threshold = _parse_stack_number(stack[1])
    signers_item = stack[2]
    if not isinstance(signers_item, dict) or signers_item.get("type") != "cell":
        raise ValueError("TONAPI returned no signer dictionary cell")
    signers_boc = signers_item.get("cell")
    signers = _read_hashmap_addresses(signers_boc)
    proposers_item = stack[3]
    if isinstance(proposers_item, dict) and proposers_item.get("type") == "null":
        proposers: list[str] = []
    elif isinstance(proposers_item, dict) and proposers_item.get("type") == "cell":
        proposers = _read_hashmap_addresses(proposers_item.get("cell"))
    else:
        raise ValueError("TONAPI returned an unexpected proposer dictionary")
    return {
        "address": _raw_address(expected_address),
        "next_order_seqno": next_order_seqno,
        "threshold": threshold,
        "signers": signers,
        "proposers": proposers,
    }


def is_two_of_three_multisig(config: Any, expected_address: str) -> bool:
    """Accept only exact treasury address, threshold 2, 3 signers, no proposers."""
    if not isinstance(config, dict):
        return False
    try:
        expected = _raw_address(expected_address)
        actual = _raw_address(str(config.get("address") or ""))
        threshold = config.get("threshold")
        signers = config.get("signers")
        proposers = config.get("proposers")
        if (
            actual != expected
            or isinstance(threshold, bool)
            or not isinstance(threshold, int)
            or threshold != 2
        ):
            return False
        if not isinstance(signers, list) or len(signers) != 3:
            return False
        normalised = [_raw_address(str(signer)) for signer in signers]
        if normalised != EXPECTED_TON_MULTISIG_SIGNERS or expected in set(normalised):
            return False
        if not isinstance(proposers, list) or proposers:
            return False
        return True
    except Exception:
        return False


async def fetch_ton_wallet_public_key(
    address: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Read a wallet public key using TONAPI on-chain get_public_key getter."""
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
        response = await session.get(
            f"/v2/blockchain/accounts/{raw_address}/methods/get_public_key",
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise ValueError("TONAPI could not execute the wallet public-key getter")
        if payload.get("exit_code") != 0:
            raise ValueError("TON wallet public-key getter failed")
        stack = payload.get("stack")
        public_key = None
        if isinstance(stack, list) and stack and isinstance(stack[0], dict):
            item = stack[0]
            if item.get("type") == "num" and isinstance(item.get("num"), str):
                number = item["num"]
                if number.startswith(("0x", "0X")):
                    try:
                        public_key = f"{int(number, 16):064x}"
                    except ValueError:
                        public_key = None
        if (
            not isinstance(public_key, str)
            or len(public_key) != 64
            or any(char not in "0123456789abcdef" for char in public_key)
        ):
            raise ValueError("TONAPI returned an invalid wallet public key")
        return public_key
    finally:
        if owns_client:
            await session.aclose()


async def fetch_ton_multisig_config(
    address: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch and decode TON Core multisig-v2's on-chain get_multisig_data."""
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
        response = await session.get(
            f"/v2/blockchain/accounts/{raw_address}/methods/get_multisig_data",
            headers=headers,
        )
        response.raise_for_status()
        return decode_multisig_v2_getter(response.json(), raw_address)
    finally:
        if owns_client:
            await session.aclose()
