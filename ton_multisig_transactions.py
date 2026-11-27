"""Deterministic TON Core multisig-v2 transaction body preparation.

This module only builds unsigned BOCs for TON Connect. It never signs, submits,
or executes a transaction. The caller must independently verify the deployed
multisig configuration and the connected wallet before presenting a request.
"""
from __future__ import annotations

import base64
import hashlib
import time
from typing import Any

from tonsdk.boc import Cell

from ton_multisig import decode_multisig_order_actions

OP_NEW_ORDER = 0xF718510F
OP_ORDER_APPROVE = 0xA762230F
AUTO_ORDER_SEQNO = (1 << 256) - 1
NANO_GRAM = 1_000_000_000
NEW_ORDER_ATTACHED_NANO = NANO_GRAM
APPROVAL_ATTACHED_NANO = 100_000_000


def _boc_cell(value: str) -> Cell:
    if not isinstance(value, str) or not value or len(value) > 200_000:
        raise ValueError("Invalid multisig order action BOC")
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) > 100_000:
            raise ValueError("Multisig order action BOC is too large")
        return Cell.one_from_boc(raw)
    except Exception as exc:
        raise ValueError("Malformed multisig order action BOC") from exc


def _unsigned_payload(cell: Cell) -> str:
    return base64.b64encode(cell.to_boc(False)).decode("ascii")


def _validate_marketplace_actions(order_cell: Cell) -> dict[str, Any]:
    decoded = decode_multisig_order_actions(_unsigned_payload(order_cell))
    actions = decoded["actions"]
    if not actions:
        raise ValueError("A multisig order must contain at least one action")
    for action in actions:
        if action.get("kind") != "send_message":
            raise ValueError("Governance and unknown actions cannot be proposed as marketplace transfers")
        if action.get("message_kind") not in {"nft_transfer", "jetton_transfer"}:
            raise ValueError("Only decoded NFT and Jetton transfers can be prepared for marketplace review")
        if not action.get("body_fields_decoded") or not action.get("asset_recipient"):
            raise ValueError("Transfer recipient could not be decoded safely")
        if action.get("extra_currencies"):
            raise ValueError("Extra-currency messages are not supported")
    return decoded


def build_new_order_payload(
    action_boc: str,
    *,
    signer_index: int,
    signer_addresses: list[str],
    expiration_date: int,
    now: int | None = None,
    query_id: int = 0,
) -> dict[str, Any]:
    """Build the unsigned body for multisig-v2 new_order, requiring a signer."""
    if isinstance(signer_index, bool) or not isinstance(signer_index, int):
        raise ValueError("Invalid signer index")
    if not isinstance(signer_addresses, list) or len(signer_addresses) != 3:
        raise ValueError("Expected the configured three-signer treasury")
    if not 0 <= signer_index < len(signer_addresses):
        raise ValueError("Connected wallet is not a configured multisig signer")
    if len({str(address).lower() for address in signer_addresses}) != 3:
        raise ValueError("Multisig signer addresses must be unique")
    if isinstance(expiration_date, bool) or not isinstance(expiration_date, int):
        raise ValueError("Invalid order expiration")
    current = int(time.time()) if now is None else int(now)
    if expiration_date <= current + 60 or expiration_date > current + 7 * 24 * 3600:
        raise ValueError("Order expiration must be between 1 minute and 7 days")
    if isinstance(query_id, bool) or not isinstance(query_id, int) or not 0 <= query_id < (1 << 64):
        raise ValueError("Invalid query id")

    order_cell = _boc_cell(action_boc)
    decoded = _validate_marketplace_actions(order_cell)

    body = Cell()
    body.bits.write_uint(OP_NEW_ORDER, 32)
    body.bits.write_uint(query_id, 64)
    body.bits.write_uint(AUTO_ORDER_SEQNO, 256)
    body.bits.write_bit(1)  # sender is a signer, not a proposer
    body.bits.write_uint(signer_index, 8)
    body.bits.write_uint(expiration_date, 48)
    body.refs.append(order_cell)

    payload = _unsigned_payload(body)
    return {
        "payload": payload,
        "payload_sha256": hashlib.sha256(base64.b64decode(payload)).hexdigest(),
        "opcode": f"0x{OP_NEW_ORDER:08x}",
        "signer_index": signer_index,
        "expiration_date": expiration_date,
        "attached_value_nano": str(NEW_ORDER_ATTACHED_NANO),
        "action_count": decoded["action_count"],
        "actions": decoded["actions"],
        "approval_safe": False,
        "requires_independent_signer_review": True,
    }


def build_order_approve_payload(*, signer_index: int, signer_count: int = 3, query_id: int = 0) -> dict[str, str]:
    """Build the unsigned body for a child Order contract approval."""
    if isinstance(signer_index, bool) or not isinstance(signer_index, int):
        raise ValueError("Invalid signer index")
    if isinstance(signer_count, bool) or not isinstance(signer_count, int) or signer_count != 3:
        raise ValueError("Unexpected multisig signer count")
    if not 0 <= signer_index < signer_count:
        raise ValueError("Connected wallet is not a configured multisig signer")
    if isinstance(query_id, bool) or not isinstance(query_id, int) or not 0 <= query_id < (1 << 64):
        raise ValueError("Invalid query id")

    body = Cell()
    body.bits.write_uint(OP_ORDER_APPROVE, 32)
    body.bits.write_uint(query_id, 64)
    body.bits.write_uint(signer_index, 8)
    payload = _unsigned_payload(body)
    return {
        "payload": payload,
        "payload_sha256": hashlib.sha256(base64.b64decode(payload)).hexdigest(),
        "opcode": f"0x{OP_ORDER_APPROVE:08x}",
        "signer_index": str(signer_index),
        "attached_value_nano": str(APPROVAL_ATTACHED_NANO),
    }
