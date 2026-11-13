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
                query_id = transfer.get("query_id")
                event_index = _stable_event_index(transfer.get("transaction_lt"), query_id,
                                                  source_wallet, transfer.get("amount"))
                mc_seqno = int(tx.get("mc_block_seqno") or 0)
                candidates.append({
                    "chain": "ton-mainnet", "asset_kind": "jetton",
                    "master_address": canonical_ton_address(invoice["master_address"]),
                    "source_jetton_wallet": source_wallet,
                    "expected_source_jetton_wallet": expected_wallet,
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


class TonApiEmbedPaymentProvider:
    """Second independent indexer; the self-hosted TonAPI Embed API must be synced."""

    provider_id = "tonapi"

    def __init__(self, *, base_url: str, api_key: str = "", timeout: float = 12.0):
        if not base_url.startswith("https://"):
            raise RuntimeError("TONAPI_EMBED_URL must be an HTTPS endpoint")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        headers = {"Authorization": "Bearer " + self.api_key} if self.api_key else {}
        return httpx.AsyncClient(timeout=self.timeout, headers=headers)

    async def _history(self, invoice: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
        asset_kind = invoice.get("asset_kind", "jetton")
        if asset_kind == "native":
            path = f"/account/{invoice['recipient_address']}/history"
        else:
            path = f"/jetton/{invoice['master_address']}/{invoice['recipient_address']}/history"
        async with self._client() as client:
            history_response = await client.get(self.base_url + path, params={"limit": 1000, "sort": "desc"})
            status_response = await client.get(self.base_url + "/status")
            account_response = await client.get(
                self.base_url + (f"/account/{invoice['recipient_address']}" if asset_kind == "native"
                                 else f"/jetton/{invoice['master_address']}/{invoice['recipient_address']}")
            )
        history_response.raise_for_status()
        status_response.raise_for_status()
        account_response.raise_for_status()
        status = status_response.json()
        account = account_response.json()
        last_processed_lt = int(account.get("last_processed_tx_lt") or 0)
        mc_seqno = int(status.get("mc_seqno") or status.get("last_mc_seqno") or 0)
        rows = history_response.json()
        if isinstance(rows, dict):
            rows = rows.get("transfers") or rows.get("history") or rows.get("items") or []
        return rows if isinstance(rows, list) else [], last_processed_lt, mc_seqno

    async def _source_wallet(self, master: str, source_owner: str) -> str | None:
        async with self._client() as client:
            response = await client.get(
                "https://tonapi.io/v2/accounts/" + source_owner + "/jettons/" + master,
                headers={"Authorization": "Bearer " + self.api_key} if self.api_key else {},
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        wallet = data.get("wallet_address") or data.get("jetton_wallet")
        return canonical_ton_address(wallet) if wallet else None

    async def find_invoice_transfer(self, invoice: dict[str, Any]) -> dict[str, Any] | None:
        candidates = await self.find_invoice_transfers(invoice)
        if len(candidates) > 1:
            raise ValueError("More than one transfer matches a unique invoice payload")
        return candidates[0] if candidates else None

    async def find_invoice_transfers(self, invoice: dict[str, Any]) -> list[dict[str, Any]]:
        if invoice.get("chain") != "ton-mainnet":
            raise ValueError("Only TON mainnet invoices are supported")
        rows, last_lt, mc_seqno = await self._history(invoice)
        candidates = []
        for row in rows:
            try:
                tx_hash = _hash64(row.get("hash") or row.get("transaction_hash"))
                lt = int(row.get("lt") or row.get("transaction_lt") or 0)
                if lt <= 0 or lt > last_lt or mc_seqno <= 0:
                    continue
                if canonical_ton_address(row.get("destination") or row.get("recipient") or "") != canonical_ton_address(invoice["recipient_address"]):
                    continue
                comment = _comment(row.get("comment") or row.get("forward_payload"))
                if comment != invoice["payload"]:
                    continue
                amount = int(row.get("amount") or 0)
                if invoice.get("asset_kind") == "native":
                    candidates.append({
                        "chain": "ton-mainnet", "asset_kind": "native", "master_address": "",
                        "sender_address": canonical_ton_address(row.get("source") or row.get("sender") or ""),
                        "recipient_address": canonical_ton_address(invoice["recipient_address"]),
                        "amount_raw": amount, "payload": comment, "opcode": None,
                        "successful": row.get("successful", row.get("success")) is True,
                        "bounced": row.get("bounced") is True,
                        "finalized": lt <= last_lt and mc_seqno > 0,
                        "tx_hash": tx_hash, "event_index": lt, "mc_block_seqno": mc_seqno,
                    })
                    continue
                if canonical_ton_address(row.get("jetton_master") or row.get("jetton") or "") != canonical_ton_address(invoice["master_address"]):
                    continue
                source_owner = canonical_ton_address(row.get("source") or row.get("sender") or "")
                source_wallet = await self._source_wallet(invoice["master_address"], source_owner)
                if not source_wallet:
                    continue
                candidates.append({
                    "chain": "ton-mainnet", "asset_kind": "jetton",
                    "master_address": canonical_ton_address(invoice["master_address"]),
                    "source_jetton_wallet": source_wallet,
                    "expected_source_jetton_wallet": source_wallet,
                    "sender_address": source_owner,
                    "recipient_address": canonical_ton_address(invoice["recipient_address"]),
                    "amount_raw": amount, "payload": comment,
                    "opcode": JETTON_TRANSFER_NOTIFICATION,
                    "successful": row.get("successful") is True,
                    "bounced": row.get("bounced") is True,
                    "finalized": lt <= last_lt and mc_seqno > 0,
                    "tx_hash": tx_hash,
                    "event_index": _stable_event_index(lt, row.get("query_id"), source_wallet, amount),
                    "mc_block_seqno": mc_seqno,
                })
            except (TypeError, ValueError, KeyError):
                continue
        return candidates


def configured_ton_payment_providers() -> list[TonPaymentProvider]:
    """Require two named independent operators and never silently degrade to one."""
    ids = {part.strip() for part in os.getenv("TON_DEPOSIT_PROVIDER_IDS", "").split(",") if part.strip()}
    if ids != {"toncenter", "tonapi"}:
        raise RuntimeError("TON_DEPOSIT_PROVIDER_IDS must be 'tonapi,toncenter'")
    return [
        ToncenterV3PaymentProvider(api_key=os.getenv("TONCENTER_API_KEY", "")),
        TonApiEmbedPaymentProvider(
            base_url=os.getenv("TONAPI_EMBED_URL", ""),
            api_key=os.getenv("TONAPI_EMBED_API_KEY", ""),
        ),
    ]
