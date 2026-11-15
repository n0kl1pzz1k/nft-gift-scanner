"""Fail-closed TON mainnet payment observations from independent indexers.

Toncenter v3 supplies raw/indexed transaction data. TonAPI Embed supplies a
separately indexed transfer history. Neither provider can settle an invoice:
the monitor requires matching finalized observations from both providers.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Protocol

import httpx

from ton_nft_providers import canonical_ton_address


JETTON_TRANSFER_NOTIFICATION = "0x7362d09c"


def _stable_event_index(*parts: Any) -> int:
    digest = hashlib.sha256("|".join(str(part or "") for part in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


def _comment(value: Any) -> str | None:
    """Extract only an explicit decoded text comment, never guess from arbitrary payload."""
    if isinstance(value, str):
        return value if value.startswith(("gri_", "grpay_", "gr_")) else None
    if isinstance(value, dict):
        for key in ("text", "comment", "value"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith(("gri_", "grpay_", "gr_")):
                return candidate
        for key in ("decoded", "message_content", "decoded_forward_payload", "forward_payload"):
            found = _comment(value.get(key))
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _comment(item)
            if found:
                return found
    return None


def _tx_success(tx: dict[str, Any]) -> bool:
    if tx.get("aborted") is True or tx.get("transaction_aborted") is True:
        return False
    description = tx.get("description") or {}
    compute = description.get("compute_ph") or description.get("compute_phase") or {}
    action = description.get("action") or description.get("action_phase") or {}
    compute_code = compute.get("exit_code")
    action_code = action.get("result_code")
    if compute_code is None or action_code is None:
        return False
    return int(compute_code) == 0 and int(action_code) == 0 and description.get("aborted") is not True


def _hash64(value: Any) -> str:
    result = str(value or "").lower().removeprefix("0x")
    if len(result) != 64 or any(ch not in "0123456789abcdef" for ch in result):
        raise ValueError("Provider returned an invalid TON transaction hash")
    return result


class TonPaymentProvider(Protocol):
    provider_id: str

    async def find_invoice_transfers(self, invoice: dict[str, Any]) -> list[dict[str, Any]]:
        ...


class ToncenterV3PaymentProvider:
    provider_id = "toncenter"

    def __init__(self, *, api_key: str, timeout: float = 12.0):
        if not api_key:
            raise RuntimeError("TONCENTER_API_KEY is required")
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = "https://toncenter.com/api/v3"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, headers={"X-API-Key": self.api_key})

    async def _transaction(self, tx_hash: str) -> dict[str, Any] | None:
        async with self._client() as client:
            response = await client.get(self.base_url + "/transactions", params={"hash": tx_hash, "limit": 1})
        response.raise_for_status()
        rows = response.json().get("transactions") or []
        return rows[0] if len(rows) == 1 else None

    async def _expected_source_wallet(self, master: str, source_owner: str) -> str | None:
        async with self._client() as client:
            response = await client.get(self.base_url + "/jetton/wallets", params={
                "owner_address": source_owner, "jetton_master": master, "limit": 10,
            })
        response.raise_for_status()
        rows = response.json().get("jetton_wallets") or []
        if len(rows) != 1:
            return None
        wallet = rows[0].get("address") or rows[0].get("jetton_wallet")
        return canonical_ton_address(wallet) if wallet else None

    async def _native(self, invoice: dict[str, Any]) -> list[dict[str, Any]]:
        async with self._client() as client:
            response = await client.get(self.base_url + "/transactions", params={
                "account": invoice["recipient_address"], "start_utime": int(invoice["created_at"]),
                "limit": 1000, "sort": "desc",
            })
        response.raise_for_status()
        candidates = []
        for tx in response.json().get("transactions") or []:
            msg = tx.get("in_msg") or {}
            destination = msg.get("destination")
            if not destination or canonical_ton_address(destination) != canonical_ton_address(invoice["recipient_address"]):
                continue
            comment = _comment(msg.get("message_content") or msg.get("decoded"))
            if comment != invoice["payload"]:
                continue
            tx_hash = _hash64(tx.get("hash"))
            mc_seqno = int(tx.get("mc_block_seqno") or 0)
            successful = _tx_success(tx)
            bounced = bool(msg.get("bounced")) or bool(tx.get("aborted"))
            candidates.append({
                "chain": "ton-mainnet", "asset_kind": "native", "master_address": "",
                "sender_address": canonical_ton_address(msg.get("source") or ""),
                "recipient_address": canonical_ton_address(destination),
                "amount_raw": int(msg.get("value") or 0), "payload": comment,
                "opcode": None, "successful": successful, "bounced": bounced,
                "finalized": mc_seqno > 0, "tx_hash": tx_hash,
                "event_index": int(tx.get("lt") or 0), "mc_block_seqno": mc_seqno,
            })
        return candidates

    async def _jettons(self, invoice: dict[str, Any]) -> list[dict[str, Any]]:
        async with self._client() as client:
            response = await client.get(self.base_url + "/jetton/transfers", params={
                "owner_address": invoice["recipient_address"], "direction": "in",
                "jetton_master": invoice["master_address"],
                "start_utime": int(invoice["created_at"]), "limit": 1000, "sort": "desc",
            })
        response.raise_for_status()
        candidates = []
        for transfer in response.json().get("jetton_transfers") or []:
            try:
                if canonical_ton_address(transfer.get("destination") or "") != canonical_ton_address(invoice["recipient_address"]):
                    continue
                if canonical_ton_address(transfer.get("jetton_master") or "") != canonical_ton_address(invoice["master_address"]):
                    continue
                comment = _comment(transfer.get("decoded_forward_payload") or transfer.get("forward_payload"))
                if comment != invoice["payload"]:
                    continue
                tx_hash = _hash64(transfer.get("transaction_hash"))
                tx = await self._transaction(tx_hash)
                if tx is None:
                    continue
                source_owner = canonical_ton_address(transfer.get("source") or "")
                source_wallet = canonical_ton_address(transfer.get("source_wallet") or "")
                expected_wallet = await self._expected_source_wallet(invoice["master_address"], source_owner)
                if not expected_wallet or expected_wallet != source_wallet:
                    continue
                recipient_owner = canonical_ton_address(invoice["recipient_address"])
                destination_wallet = canonical_ton_address(transfer.get("destination_wallet") or "")
                expected_destination_wallet = await self._expected_source_wallet(
                    invoice["master_address"], recipient_owner,
                )
                if not expected_destination_wallet or destination_wallet != expected_destination_wallet:
                    continue
                query_id = transfer.get("query_id")
                event_index = _stable_event_index(transfer.get("transaction_lt"), query_id,
                                                  source_wallet, transfer.get("amount"))
                mc_seqno = int(tx.get("mc_block_seqno") or 0)
                candidates.append({
                    "chain": "ton-mainnet", "asset_kind": "jetton",
                    "master_address": canonical_ton_address(invoice["master_address"]),
                    "source_jetton_wallet": source_wallet,
                    "expected_source_jetton_wallet": expected_wallet,
                    "destination_jetton_wallet": destination_wallet,
                    "expected_destination_jetton_wallet": expected_destination_wallet,
                    "sender_address": source_owner,
                    "recipient_address": canonical_ton_address(transfer["destination"]),
                    "amount_raw": int(transfer.get("amount") or 0),
                    "payload": comment, "opcode": JETTON_TRANSFER_NOTIFICATION,
                    "successful": _tx_success(tx) and transfer.get("transaction_aborted") is False,
                    "bounced": bool(transfer.get("transaction_aborted")),
                    "finalized": mc_seqno > 0, "tx_hash": tx_hash,
                    "event_index": event_index, "mc_block_seqno": mc_seqno,
                })
            except (TypeError, ValueError, KeyError):
                continue
        return candidates

    async def find_invoice_transfer(self, invoice: dict[str, Any]) -> dict[str, Any] | None:
        candidates = await self.find_invoice_transfers(invoice)
        if len(candidates) > 1:
            raise ValueError("More than one transfer matches a unique invoice payload")
        return candidates[0] if candidates else None

    async def find_invoice_transfers(self, invoice: dict[str, Any]) -> list[dict[str, Any]]:
        if invoice.get("chain") != "ton-mainnet":
            raise ValueError("Only TON mainnet invoices are supported")
        if invoice.get("asset_kind") == "native":
            return await self._native(invoice)
        return await self._jettons(invoice)


class TonApiPaymentProvider:
    """Independent read-only observer using the official TonAPI mainnet REST API."""

    provider_id = "tonapi"

    def __init__(self, *, api_key: str, timeout: float = 12.0):
        if not api_key:
            raise RuntimeError("TONAPI_API_KEY is required")
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = "https://tonapi.io/v2"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            headers={"Authorization": "Bearer " + self.api_key},
        )

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.get(self.base_url + path, params=params)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("TonAPI returned a non-object response")
        return data

    @staticmethod
    def _address(value: Any) -> str:
        if isinstance(value, dict):
            value = value.get("address")
        if not isinstance(value, str) or not value:
            raise ValueError("TonAPI response omitted a required TON address")
        return canonical_ton_address(value)

    @staticmethod
    def _transaction_successful(tx: dict[str, Any]) -> bool:
        if tx.get("success") is not True or tx.get("aborted") is not False:
            return False
        compute = tx.get("compute_phase")
        if not isinstance(compute, dict) or compute.get("success") is not True:
            return False
        if int(compute.get("exit_code", -1)) != 0:
            return False
        action = tx.get("action_phase")
        if action is not None and (
            not isinstance(action, dict) or action.get("success") is not True
            or int(action.get("result_code", -1)) != 0
        ):
            return False
        return tx.get("bounce_phase") in (None, "")

    async def _status(self) -> dict[str, Any]:
        status = await self._get("/status")
        if status.get("rest_online") is not True:
            raise RuntimeError("TonAPI mainnet REST service is not online")
        if int(status.get("last_known_masterchain_seqno") or 0) <= 0:
            raise RuntimeError("TonAPI has no known mainnet masterchain head")
        return status

    async def _jetton_wallet(self, owner: str, master: str) -> str | None:
        owner_path = quote(owner, safe=":")
        master_path = quote(master, safe=":")
        async with self._client() as client:
            response = await client.get(
                f"{self.base_url}/accounts/{owner_path}/jettons/{master_path}"
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        wallet = data.get("wallet_address") if isinstance(data, dict) else None
        return canonical_ton_address(wallet) if wallet else None

    async def _transaction(self, tx_hash: str) -> dict[str, Any] | None:
        async with self._client() as client:
            response = await client.get(
                self.base_url + "/blockchain/transactions/" + quote(tx_hash, safe="")
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else None

    async def _native(self, invoice: dict[str, Any]) -> list[dict[str, Any]]:
        recipient = canonical_ton_address(invoice["recipient_address"])
        recipient_path = quote(recipient, safe=":")
        data = await self._get(
            f"/blockchain/accounts/{recipient_path}/transactions",
            {"limit": 1000, "sort_order": "desc"},
        )
        status = await self._status()
        candidates = []
        for tx in data.get("transactions") or []:
            try:
                msg = tx.get("in_msg") or {}
                if self._address(msg.get("destination")) != recipient:
                    continue
                if int(tx.get("utime") or 0) < int(invoice["created_at"]):
                    continue
                comment = _comment(msg.get("decoded_body"))
                if comment != invoice["payload"]:
                    continue
                tx_hash = _hash64(tx.get("hash"))
                successful = self._transaction_successful(tx) and msg.get("bounced") is not True
                candidates.append({
                    "chain": "ton-mainnet", "asset_kind": "native", "master_address": "",
                    "sender_address": self._address(msg.get("source")),
                    "recipient_address": recipient,
                    "amount_raw": int(msg.get("value") or 0), "payload": comment,
                    "opcode": None, "successful": successful,
                    "bounced": msg.get("bounced") is True,
                    "finalized": bool(tx.get("block")) and int(status["last_known_masterchain_seqno"]) > 0,
                    "tx_hash": tx_hash, "event_index": int(tx.get("lt") or 0),
                    "mc_block_seqno": int(status["last_known_masterchain_seqno"]),
                })
            except (TypeError, ValueError, KeyError):
                continue
        return candidates

    async def _jettons(self, invoice: dict[str, Any]) -> list[dict[str, Any]]:
        recipient = canonical_ton_address(invoice["recipient_address"])
        master = canonical_ton_address(invoice["master_address"])
        recipient_path = quote(recipient, safe=":")
        master_path = quote(master, safe=":")
        history = await self._get(
            f"/jettons/{master_path}/accounts/{recipient_path}/history",
            {"limit": 1000, "start_date": int(invoice["created_at"])},
        )
        status = await self._status()
        expected_destination_wallet = await self._jetton_wallet(recipient, master)
        if not expected_destination_wallet:
            return []
        candidates = []
        for row in history.get("operations") or []:
            try:
                if row.get("operation") != "transfer":
                    continue
                if self._address(row.get("destination")) != recipient:
                    continue
                jetton = row.get("jetton") or {}
                if self._address(jetton.get("address")) != master:
                    continue
                amount = int(row.get("amount") or 0)
                query_id = int(row.get("query_id") or 0)
                if amount <= 0 or query_id <= 0:
                    continue
                expected_query_id = invoice.get("query_id")
                if expected_query_id is not None and query_id != int(expected_query_id):
                    continue
                tx_hash = _hash64(row.get("transaction_hash"))
                source_owner = self._address(row.get("source"))
                expected_source_wallet = await self._jetton_wallet(source_owner, master)
                if not expected_source_wallet:
                    continue
                tx = await self._transaction(tx_hash)
                if tx is None or not self._transaction_successful(tx):
                    continue
                msg = tx.get("in_msg") or {}
                if msg.get("bounced") is True or str(msg.get("op_code") or "").lower() != JETTON_TRANSFER_NOTIFICATION:
                    continue
                if self._address(tx.get("account")) != expected_destination_wallet:
                    continue
                if self._address(msg.get("source")) != expected_source_wallet:
                    continue
                if self._address(msg.get("destination")) != expected_destination_wallet:
                    continue
                body = msg.get("decoded_body") or {}
                body_query_id = int(body.get("query_id") or 0)
                body_amount = int(body.get("amount") or 0)
                body_sender = self._address(body.get("sender"))
                comment = _comment(body)
                if (body_query_id != query_id or body_amount != amount
                        or body_sender != source_owner or comment != invoice["payload"]):
                    continue
                lt = int(row.get("lt") or tx.get("lt") or 0)
                if lt <= 0:
                    continue
                candidates.append({
                    "chain": "ton-mainnet", "asset_kind": "jetton",
                    "master_address": master,
                    "source_jetton_wallet": expected_source_wallet,
                    "expected_source_jetton_wallet": expected_source_wallet,
                    "destination_jetton_wallet": expected_destination_wallet,
                    "expected_destination_jetton_wallet": expected_destination_wallet,
                    "sender_address": source_owner, "recipient_address": recipient,
                    "amount_raw": amount, "payload": comment,
                    "opcode": JETTON_TRANSFER_NOTIFICATION,
                    "successful": True, "bounced": False,
                    "finalized": bool(tx.get("block")) and int(status["last_known_masterchain_seqno"]) > 0,
                    "tx_hash": tx_hash,
                    "event_index": _stable_event_index(lt, query_id, expected_source_wallet, amount),
                    "mc_block_seqno": int(status["last_known_masterchain_seqno"]),
                })
            except (TypeError, ValueError, KeyError):
                continue
        return candidates

    async def find_invoice_transfer(self, invoice: dict[str, Any]) -> dict[str, Any] | None:
        candidates = await self.find_invoice_transfers(invoice)
        if len(candidates) > 1:
            raise ValueError("TonAPI returned multiple transfers for one unique invoice")
        return candidates[0] if candidates else None

    async def find_invoice_transfers(self, invoice: dict[str, Any]) -> list[dict[str, Any]]:
        if invoice.get("chain") != "ton-mainnet":
            raise ValueError("TonAPI mainnet provider accepts only TON mainnet invoices")
        if invoice.get("asset_kind") == "native":
            return await self._native(invoice)
        return await self._jettons(invoice)


def configured_ton_payment_providers() -> list[TonPaymentProvider]:
    """Require two named independent operators and never silently degrade to one."""
    ids = {part.strip() for part in os.getenv("TON_DEPOSIT_PROVIDER_IDS", "").split(",") if part.strip()}
    if ids != {"toncenter", "tonapi"}:
        raise RuntimeError("TON_DEPOSIT_PROVIDER_IDS must be 'tonapi,toncenter'")
    return [
        ToncenterV3PaymentProvider(api_key=os.getenv("TONCENTER_API_KEY", "")),
        TonApiPaymentProvider(api_key=os.getenv("TONAPI_API_KEY", "")),
    ]
