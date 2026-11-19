"""Read-only TON NFT contract verification helpers.

Collection membership is checked against the contracts themselves:
the NFT item's get_nft_data() and the collection's
get_nft_address_by_index(index). No transaction is sent.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

TONCENTER_BASE_URL = "https://toncenter.com"


def _raw_address(value: str) -> str:
    from tonsdk.utils import Address

    address = Address(value)
    return f"{int(address.wc)}:{bytes(address.hash_part).hex()}"


def _stack_number(entry: Any) -> int:
    if not isinstance(entry, (list, tuple)) or len(entry) != 2 or entry[0] != "num":
        raise ValueError("TON Center returned an invalid numeric stack entry")
    try:
        return int(str(entry[1]), 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("TON Center returned an invalid numeric stack value") from exc


def _stack_address(entry: Any) -> str | None:
    import base64
    from tonsdk.boc import Cell

    if not isinstance(entry, (list, tuple)) or len(entry) != 2 or entry[0] != "slice":
        raise ValueError("TON Center returned an invalid address stack entry")
    try:
        boc = base64.b64decode(str(entry[1]), validate=True)
        cell = Cell.one_from_boc(boc)
        address_slice = cell.begin_parse()
        tag = address_slice.read_uint(2)
        if tag == 0:
            return None
        if tag != 2:
            raise ValueError("Only standard internal TON addresses are accepted")
        if address_slice.read_bit():
            raise ValueError("Anycast TON addresses are not accepted")
        workchain = address_slice.read_int(8)
        account_hash = address_slice.read_bytes(32)
        if len(account_hash) != 32:
            raise ValueError("Invalid TON account hash")
        return f"{workchain}:{account_hash.hex()}"
    except Exception as exc:
        raise ValueError("TON Center returned an invalid address cell") from exc


async def _run_get_method(
    session: httpx.AsyncClient,
    address: str,
    method: str,
    stack: list[list[str]],
    *,
    seqno: int | None = None,
) -> tuple[list[Any], int | None]:
    token = os.getenv("TONCENTER_API_KEY", "").strip()
    headers = {"Accept": "application/json"}
    if token:
        headers["X-API-Key"] = token
    payload = {"address": _raw_address(address), "method": method, "stack": stack}
    if seqno is not None:
        payload["seqno"] = seqno
    response = await session.post(
        "/api/v2/runGetMethod",
        json=payload,
        headers=headers,
    )
    response.raise_for_status()
    body = response.json()
    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(body, dict) or body.get("ok") is not True or not isinstance(result, dict):
        raise ValueError("TON Center could not execute the NFT contract getter")
    if result.get("exit_code") != 0:
        raise ValueError("TON NFT contract getter returned a non-zero exit code")
    values = result.get("stack")
    if not isinstance(values, list):
        raise ValueError("TON Center returned an invalid getter stack")
    block = result.get("block_id") if isinstance(result.get("block_id"), dict) else {}
    block_seqno = block.get("seqno")
    if block_seqno is not None and (not isinstance(block_seqno, int) or block_seqno < 0):
        raise ValueError("TON Center returned an invalid block sequence number")
    return values, block_seqno


async def verify_nft_collection_membership(
    item_address: str,
    expected_collection_address: str,
    expected_owner_address: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Verify initialized item, current owner, and collection-derived item address.

    Provider errors are raised so callers can fail closed. A valid response with
    mismatched owner or collection returns verified=False.
    """
    item = _raw_address(item_address)
    expected_collection = _raw_address(expected_collection_address)
    expected_owner = _raw_address(expected_owner_address)

    owns_client = client is None
    session = client or httpx.AsyncClient(
        base_url=TONCENTER_BASE_URL,
        timeout=httpx.Timeout(12.0, connect=5.0),
        follow_redirects=False,
    )
    try:
        item_stack, block_seqno = await _run_get_method(session, item, "get_nft_data", [])
        if block_seqno is None:
            raise ValueError("TON Center did not return a block height for consistent verification")
        if len(item_stack) < 4:
            raise ValueError("TON NFT item getter returned an incomplete stack")
        initialized = _stack_number(item_stack[0]) != 0
        index = _stack_number(item_stack[1])
        actual_collection = _stack_address(item_stack[2])
        actual_owner = _stack_address(item_stack[3])

        if not initialized or actual_collection != expected_collection or actual_owner != expected_owner:
            return {
                "verified": False,
                "item_address": item,
                "collection_address": actual_collection,
                "owner_address": actual_owner,
                "index": index,
            }

        if not os.getenv("TONCENTER_API_KEY", "").strip():
            # Public TON Center API v2 is limited to one request per second.
            await asyncio.sleep(1.1)
        collection_stack, _ = await _run_get_method(
            session,
            expected_collection,
            "get_nft_address_by_index",
            [["num", str(index)]],
            seqno=block_seqno,
        )
        if not collection_stack:
            raise ValueError("TON collection getter returned an empty stack")
        derived_item = _stack_address(collection_stack[0])
        return {
            "verified": derived_item == item,
            "item_address": item,
            "collection_address": actual_collection,
            "owner_address": actual_owner,
            "index": index,
            "block_seqno": block_seqno,
            "derived_item_address": derived_item,
        }
    finally:
        if owns_client:
            await session.aclose()
