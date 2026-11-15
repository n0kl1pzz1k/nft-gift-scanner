"""Independent TON testnet observation for platform-issued tUSDT deposits.

Read-only: this module never signs, broadcasts, or accepts wallet secrets.
A payment is returned only when TON Center v3 and the official TonAPI testnet API independently
report the same finalized Jetton transfer with the exact deal reference/amount.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

import httpx

from payment_routes import PaymentEvidence, PaymentIntent, PaymentRouteError
from ton_nft_providers import canonical_ton_address


@dataclass(frozen=True)
class TestnetTransfer:
    provider_id: str
    transaction_id: str
    deal_id: str
    reference: str
    amount: int
    currency: str
    network: str
    sender: str
    recipient: str
    finalized: bool
    query_id: int


class TransferProvider(Protocol):
    provider_id: str

    async def get_jetton_wallet_address(self, *, owner_address: str,
                                         jetton_master: str) -> str | None: ...

    async def find_transfer(self, *, escrow_address: str, jetton_master: str,
                            reference: str) -> TestnetTransfer | None: ...

    async def find_payout(self, *, escrow_address: str, jetton_master: str,
                          recipient_address: str, query_id: int, amount: int,
                          reference: str) -> TestnetTransfer | None: ...


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _hash(value: Any) -> str:
    raw = str(value or "").strip()
    hex_value = raw.lower().removeprefix("0x")
    if len(hex_value) == 64 and all(ch in "0123456789abcdef" for ch in hex_value):
        return hex_value
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (ValueError, TypeError):
        return ""
    return decoded.hex() if len(decoded) == 32 else ""


def _deal_id_from_reference(reference: str) -> str:
    parts = reference.split(":")
    if len(parts) >= 2 and parts[0] in {"grpay", "grpayout", "grrefund"}:
        return parts[1]
    return ""


def _comment(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        if value.get("type") == "text":
            return str(value.get("text") or "").strip()
        return str(value.get("comment") or value.get("text") or "").strip()
    return ""


def _rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _seqno(payload: dict[str, Any], *keys: str) -> int:
    value = _pick(payload, *keys)
    if isinstance(value, dict):
        value = value.get("seqno")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _account_address(value: Any) -> str:
    if isinstance(value, dict):
        return str(_pick(value, "address", "account_id") or "")
    return str(value or "")


def _same_address(left: Any, right: Any) -> bool:
    try:
        return canonical_ton_address(str(left)) == canonical_ton_address(str(right))
    except (TypeError, ValueError):
        return False


class TonCenterTestnetProvider:
    provider_id = "toncenter"

    def __init__(self, *, base_url: str, api_key: str = "", timeout: float = 12.0):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or parsed.hostname != "testnet.toncenter.com"
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise PaymentRouteError("TON Center provider must use the official HTTPS testnet host")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    async def get_jetton_wallet_address(self, *, owner_address: str,
                                         jetton_master: str) -> str | None:
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            response = await client.get(self.base_url + "/jetton/wallets", params={
                "owner_address": owner_address,
                "jetton_address": jetton_master,
                "limit": 10,
            })
        response.raise_for_status()
        for row in _rows(response.json(), "jetton_wallets"):
            wallet = _pick(row, "address", "jetton_wallet_address")
            owner = _pick(row, "owner", "owner_address")
            master = _pick(row, "jetton", "jetton_master")
            if wallet and _same_address(owner, owner_address) and _same_address(master, jetton_master):
                return str(wallet)
        return None

    async def find_transfer(self, *, escrow_address: str, jetton_master: str,
                            reference: str) -> TestnetTransfer | None:
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            history, states, masterchain = await asyncio.gather(
                client.get(self.base_url + "/jetton/transfers", params={
                    "jetton_master": jetton_master, "owner_address": escrow_address,
                    "direction": "in", "limit": 1000, "sort": "desc",
                }),
                client.get(self.base_url + "/accountStates", params={
                    "address": escrow_address, "include_boc": "false",
                }),
                client.get(self.base_url + "/masterchainInfo"),
            )
        for response in (history, states, masterchain):
            response.raise_for_status()
        state_rows = _rows(states.json(), "accounts")
        if not state_rows:
            return None
        account_lt = int(_pick(state_rows[0], "last_transaction_lt", "last_transaction_logical_time") or 0)
        mc_seqno = _seqno(masterchain.json(), "last", "last_mc_block_seqno", "seqno")
        for row in _rows(history.json(), "jetton_transfers", "transfers"):
            if row.get("transaction_aborted") is True:
                continue
            if _comment(_pick(row, "comment", "forward_payload", "payload")) != reference:
                continue
            tx = _hash(_pick(row, "transaction_hash", "tx_hash", "hash"))
            lt = int(_pick(row, "transaction_lt", "lt") or 0)
            amount = int(_pick(row, "amount") or 0)
            master = _pick(row, "jetton_master", "master")
            recipient = _pick(row, "destination", "recipient", "owner_address") or escrow_address
            if (not tx or not lt or amount <= 0 or not master
                    or not _same_address(master, jetton_master)
                    or not _same_address(recipient, escrow_address)):
                continue
            return TestnetTransfer(
                provider_id=self.provider_id, transaction_id=tx,
                deal_id=_deal_id_from_reference(reference), reference=reference,
                amount=amount, currency="tUSDT", network="testnet",
                sender=str(_pick(row, "source_owner", "sender_owner", "source", "sender") or ""),
                recipient=str(recipient),
                finalized=bool(mc_seqno > 0 and account_lt >= lt),
                query_id=int(_pick(row, "query_id") or 0),
            )
        return None

    async def find_payout(self, *, escrow_address: str, jetton_master: str,
                          recipient_address: str, query_id: int, amount: int,
                          reference: str) -> TestnetTransfer | None:
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            history, states, masterchain = await asyncio.gather(
                client.get(self.base_url + "/jetton/transfers", params={
                    "jetton_master": jetton_master, "owner_address": escrow_address,
                    "direction": "out", "limit": 1000, "sort": "desc",
                }),
                client.get(self.base_url + "/accountStates", params={
                    "address": escrow_address, "include_boc": "false",
                }),
                client.get(self.base_url + "/masterchainInfo"),
            )
        for response in (history, states, masterchain):
            response.raise_for_status()
        state_rows = _rows(states.json(), "accounts")
        if not state_rows:
            return None
        account_lt = int(_pick(state_rows[0], "last_transaction_lt", "last_transaction_logical_time") or 0)
        mc_seqno = _seqno(masterchain.json(), "last", "last_mc_block_seqno", "seqno")
        for row in _rows(history.json(), "jetton_transfers", "transfers"):
            if row.get("transaction_aborted") is True:
                continue
            if int(_pick(row, "query_id") or 0) != query_id:
                continue
            tx = _hash(_pick(row, "transaction_hash", "tx_hash", "hash"))
            lt = int(_pick(row, "transaction_lt", "lt") or 0)
            row_amount = int(_pick(row, "amount") or 0)
            master = _pick(row, "jetton_master", "master")
            sender = _pick(row, "source_owner", "sender_owner", "source", "sender")
            recipient = _pick(row, "destination_owner", "recipient_owner", "destination", "recipient")
            if (not tx or not lt or row_amount != amount or not master
                    or not _same_address(master, jetton_master)
                    or not _same_address(sender, escrow_address)
                    or not _same_address(recipient, recipient_address)):
                continue
            return TestnetTransfer(
                provider_id=self.provider_id, transaction_id=tx,
                deal_id=_deal_id_from_reference(reference), reference=reference,
                amount=row_amount, currency="tUSDT", network="testnet",
                sender=str(sender), recipient=str(recipient),
                finalized=bool(mc_seqno > 0 and account_lt >= lt),
                query_id=query_id,
            )
        return None


class TonApiTestnetProvider:
    """Read-only provider using the official public TonAPI testnet REST API."""

    provider_id = "tonapi"

    def __init__(self, *, base_url: str = "https://testnet.tonapi.io/v2",
                 api_key: str = "", timeout: float = 12.0):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or parsed.hostname != "testnet.tonapi.io"
                or parsed.path.rstrip("/") != "/v2"
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise PaymentRouteError("TonAPI must use the official HTTPS testnet API endpoint")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    async def _history(self, *, escrow_address: str, jetton_master: str) -> list[dict[str, Any]]:
        headers = {"Authorization": "Bearer " + self.api_key} if self.api_key else {}
        master = quote(jetton_master, safe=":")
        owner = quote(escrow_address, safe=":")
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            response = await client.get(
                f"{self.base_url}/jettons/{master}/accounts/{owner}/history",
                params={"limit": 1000},
            )
        response.raise_for_status()
        return _rows(response.json(), "operations")

    async def find_transfer(self, *, escrow_address: str, jetton_master: str,
                            reference: str) -> TestnetTransfer | None:
        deal_id = _deal_id_from_reference(reference)
        if not deal_id or not reference.startswith("grpay:"):
            return None
        expected_query_id = int.from_bytes(
            hashlib.sha256(f"{deal_id}:payment".encode()).digest()[:8], "big"
        ) or 1
        rows = await self._history(escrow_address=escrow_address, jetton_master=jetton_master)
        matches = []
        for row in rows:
            if row.get("operation") != "transfer":
                continue
            try:
                query_id = int(row.get("query_id") or 0)
                amount = int(row.get("amount") or 0)
                lt = int(row.get("lt") or 0)
            except (TypeError, ValueError):
                continue
            tx = _hash(row.get("transaction_hash"))
            master = _account_address(row.get("jetton"))
            sender = _account_address(row.get("source"))
            recipient = _account_address(row.get("destination"))
            if (query_id != expected_query_id or amount <= 0 or lt <= 0 or not tx
                    or not _same_address(master, jetton_master)
                    or not _same_address(recipient, escrow_address) or not sender):
                continue
            matches.append(TestnetTransfer(
                provider_id=self.provider_id, transaction_id=tx,
                deal_id=deal_id, reference=reference, amount=amount,
                currency="tUSDT", network="testnet", sender=sender,
                recipient=recipient, finalized=True, query_id=query_id,
            ))
        if len(matches) > 1:
            raise PaymentRouteError("TonAPI returned multiple transfers for one payment query ID")
        return matches[0] if matches else None

    async def find_payout(self, *, escrow_address: str, jetton_master: str,
                          recipient_address: str, query_id: int, amount: int,
                          reference: str) -> TestnetTransfer | None:
        rows = await self._history(escrow_address=escrow_address, jetton_master=jetton_master)
        matches = []
        for row in rows:
            if row.get("operation") != "transfer":
                continue
            try:
                row_query_id = int(row.get("query_id") or 0)
                row_amount = int(row.get("amount") or 0)
                lt = int(row.get("lt") or 0)
            except (TypeError, ValueError):
                continue
            tx = _hash(row.get("transaction_hash"))
            master = _account_address(row.get("jetton"))
            sender = _account_address(row.get("source"))
            recipient = _account_address(row.get("destination"))
            if (row_query_id != query_id or row_amount != amount or lt <= 0 or not tx
                    or not _same_address(master, jetton_master)
                    or not _same_address(sender, escrow_address)
                    or not _same_address(recipient, recipient_address)):
                continue
            matches.append(TestnetTransfer(
                provider_id=self.provider_id, transaction_id=tx,
                deal_id=_deal_id_from_reference(reference), reference=reference,
                amount=row_amount, currency="tUSDT", network="testnet",
                sender=sender, recipient=recipient, finalized=True,
                query_id=row_query_id,
            ))
        if len(matches) > 1:
            raise PaymentRouteError("TonAPI returned multiple transfers for one payout query ID")
        return matches[0] if matches else None


class TonTestnetPaymentGateway:
    """Read-only independent-provider gateway for the platform test Jetton."""

    def __init__(self, *, providers: list[TransferProvider], escrow_address: str,
                 jetton_master: str, escrow_jetton_wallet_address: str):
        provider_ids = {provider.provider_id for provider in providers}
        if provider_ids != {"toncenter", "tonapi"} or len(providers) != 2:
            raise PaymentRouteError("Exactly two independent testnet providers are required")
        if not escrow_address or not jetton_master or not escrow_jetton_wallet_address:
            raise PaymentRouteError("Testnet escrow, test Jetton master and escrow Jetton wallet are required")
        self.providers = providers
        self.escrow_address = canonical_ton_address(escrow_address)
        self.jetton_master = canonical_ton_address(jetton_master)
        self.escrow_jetton_wallet_address = canonical_ton_address(escrow_jetton_wallet_address)
        if not all(value.startswith("0:") for value in (
            self.escrow_address, self.jetton_master, self.escrow_jetton_wallet_address,
        )):
            raise PaymentRouteError("Testnet Jetton escrow addresses must be basechain addresses")

    async def create_intent(self, *, deal_id: str, amount: int, currency: str,
                            escrow_address: str, jetton_master: str,
                            payer_address: str | None = None) -> PaymentIntent:
        if escrow_address != self.escrow_address or jetton_master != self.jetton_master:
            raise PaymentRouteError("Payment intent does not match platform testnet registry")
        if not deal_id or amount <= 0 or currency != "tUSDT":
            raise PaymentRouteError("Only positive tUSDT testnet deal intents are supported")
        query_id = int.from_bytes(
            hashlib.sha256(f"{deal_id}:payment".encode()).digest()[:8], "big"
        ) or 1
        center = next(provider for provider in self.providers if provider.provider_id == "toncenter")
        observed_escrow_jetton_wallet = await center.get_jetton_wallet_address(
            owner_address=self.escrow_address, jetton_master=self.jetton_master,
        )
        if (not observed_escrow_jetton_wallet
                or not _same_address(observed_escrow_jetton_wallet, self.escrow_jetton_wallet_address)):
            raise PaymentRouteError("Configured escrow Jetton wallet does not match TON Center testnet state")
        payer_jetton_wallet = None
        if payer_address:
            payer_jetton_wallet = await center.get_jetton_wallet_address(
                owner_address=payer_address, jetton_master=self.jetton_master,
            )
            if not payer_jetton_wallet:
                raise PaymentRouteError("Buyer tUSDT Jetton wallet is not indexed on TON testnet")
        return PaymentIntent(
            deal_id=deal_id, amount=amount, currency="tUSDT",
            reference="grpay:" + deal_id, network="testnet", payer_address=payer_address,
            query_id=query_id, payer_jetton_wallet_address=payer_jetton_wallet,
            escrow_jetton_wallet_address=self.escrow_jetton_wallet_address,
        )

    async def verify_payment(self, intent: PaymentIntent) -> PaymentEvidence | None:
        if intent.network != "testnet" or intent.currency != "tUSDT":
            raise PaymentRouteError("Only testnet tUSDT intents can be verified")
        observations = await asyncio.gather(*[
            provider.find_transfer(
                escrow_address=self.escrow_address,
                jetton_master=self.jetton_master,
                reference=intent.reference,
            )
            for provider in self.providers
        ])
        if any(item is None for item in observations):
            return None
        first, second = observations
        assert first is not None and second is not None
        center = first if first.provider_id == "toncenter" else second
        tonapi = second if second.provider_id == "tonapi" else first
        def comparable(item):
            return (
                item.transaction_id, item.deal_id, item.reference, item.amount,
                item.currency, item.network, item.sender, item.recipient,
                item.finalized, item.query_id,
            )
        if comparable(first) != comparable(second):
            raise PaymentRouteError("TON testnet providers disagree on payment evidence")
        if (not center.finalized or not tonapi.finalized
                or center.amount != intent.amount or center.deal_id != intent.deal_id
                or center.currency != "tUSDT" or center.network != "testnet"
                or center.query_id <= 0
                or (intent.query_id is not None and center.query_id != intent.query_id)):
            return None
        if center.reference != intent.reference:
            return None
        if intent.payer_address and not _same_address(center.sender, intent.payer_address):
            return None
        return PaymentEvidence(
            deal_id=center.deal_id, reference=center.reference, amount=center.amount,
            currency="tUSDT", transaction_id=center.transaction_id,
            finalized=True, network="testnet", query_id=center.query_id,
        )

    async def verify_payout(self, intent: PaymentIntent, *, seller_address: str,
                            query_id: int) -> PaymentEvidence | None:
        if intent.network != "testnet" or intent.currency != "tUSDT" or query_id <= 0:
            raise PaymentRouteError("Only positive-query-id tUSDT testnet payouts can be verified")
        reference = f"grpayout:{intent.deal_id}:{query_id}"
        observations = await asyncio.gather(*[
            provider.find_payout(
                escrow_address=self.escrow_address,
                jetton_master=self.jetton_master,
                recipient_address=seller_address,
                query_id=query_id,
                amount=intent.amount,
                reference=reference,
            )
            for provider in self.providers
        ])
        if any(item is None for item in observations):
            return None
        first, second = observations
        assert first is not None and second is not None
        def comparable(item):
            return (
                item.transaction_id, item.deal_id, item.reference, item.amount,
                item.currency, item.network, item.sender, item.recipient,
                item.finalized, item.query_id,
            )
        if comparable(first) != comparable(second):
            raise PaymentRouteError("TON testnet providers disagree on seller payout evidence")
        if (not first.finalized or first.amount != intent.amount
                or first.currency != "tUSDT" or first.network != "testnet"
                or first.deal_id != intent.deal_id or first.reference != reference
                or first.query_id != query_id
                or not _same_address(first.sender, self.escrow_address)):
            return None
        if not _same_address(first.recipient, seller_address):
            return None
        return PaymentEvidence(
            deal_id=first.deal_id, reference=first.reference, amount=first.amount,
            currency="tUSDT", transaction_id=first.transaction_id,
            finalized=True, network="testnet", query_id=query_id,
        )

    async def verify_refund(self, intent: PaymentIntent, *, buyer_address: str,
                            query_id: int) -> PaymentEvidence | None:
        if intent.network != "testnet" or intent.currency != "tUSDT" or query_id <= 0:
            raise PaymentRouteError("Only positive-query-id tUSDT testnet refunds can be verified")
        reference = f"grrefund:{intent.deal_id}:{query_id}"
        observations = await asyncio.gather(*[
            provider.find_payout(
                escrow_address=self.escrow_address,
                jetton_master=self.jetton_master,
                recipient_address=buyer_address,
                query_id=query_id,
                amount=intent.amount,
                reference=reference,
            )
            for provider in self.providers
        ])
        if any(item is None for item in observations):
            return None
        first, second = observations
        assert first is not None and second is not None

        def comparable(item):
            return (
                item.transaction_id, item.deal_id, item.reference, item.amount,
                item.currency, item.network, item.sender, item.recipient,
                item.finalized, item.query_id,
            )

        if comparable(first) != comparable(second):
            raise PaymentRouteError("TON testnet providers disagree on buyer refund evidence")
        if (not first.finalized or first.amount != intent.amount
                or first.currency != "tUSDT" or first.network != "testnet"
                or first.deal_id != intent.deal_id or first.reference != reference
                or first.query_id != query_id
                or not _same_address(first.sender, self.escrow_address)):
            return None
        if not _same_address(first.recipient, buyer_address):
            return None
        return PaymentEvidence(
            deal_id=first.deal_id, reference=first.reference, amount=first.amount,
            currency="tUSDT", transaction_id=first.transaction_id,
            finalized=True, network="testnet", query_id=query_id,
        )

    async def refund(self, evidence: PaymentEvidence) -> str:
        raise PaymentRouteError(
            "Automated Jetton refunds are not implemented; use escrow dispute resolution"
        )


def configured_ton_testnet_gateway() -> TonTestnetPaymentGateway:
    escrow = os.getenv("TON_TESTNET_ESCROW_ADDRESS", "").strip()
    master = os.getenv("TON_TESTNET_USDT_MASTER", "").strip()
    escrow_jetton_wallet = os.getenv("TON_TESTNET_ESCROW_JETTON_WALLET", "").strip()
    center_url = os.getenv("TON_TESTNET_TONCENTER_URL", "https://testnet.toncenter.com/api/v3").strip()
    tonapi_url = os.getenv("TON_TESTNET_TONAPI_URL", "https://testnet.tonapi.io/v2").strip()
    tonapi_key = os.getenv("TON_TESTNET_TONAPI_KEY", "").strip()
    center_key = os.getenv("TON_TESTNET_TONCENTER_API_KEY", "").strip()
    if not center_key or not tonapi_key:
        raise PaymentRouteError("Dedicated TON testnet API keys for Toncenter and TonAPI are required")
    return TonTestnetPaymentGateway(
        providers=[
            TonCenterTestnetProvider(
                base_url=center_url, api_key=center_key,
            ),
            TonApiTestnetProvider(base_url=tonapi_url, api_key=tonapi_key),
        ],
        escrow_address=escrow, jetton_master=master,
        escrow_jetton_wallet_address=escrow_jetton_wallet,
    )
