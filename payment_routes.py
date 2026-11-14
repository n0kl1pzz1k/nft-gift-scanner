"""Pluggable payment-route boundary.

The deal core depends on PaymentRoute only. Stars stays disabled until a
separately approved Mini App route exists. TON/USDT here means the platform's
test Jetton (tUSDT) on TON testnet, never mainnet Tether USDT.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
import os


class PaymentRouteError(RuntimeError):
    """Raised when a route is disabled or cannot safely process a request."""


@dataclass(frozen=True)
class PaymentIntent:
    deal_id: str
    amount: int
    currency: str
    reference: str
    network: str
    payer_address: str | None = None


@dataclass(frozen=True)
class PaymentEvidence:
    deal_id: str
    reference: str
    amount: int
    currency: str
    transaction_id: str
    finalized: bool
    network: str
    query_id: int | None = None


@runtime_checkable
class PaymentRoute(Protocol):
    route_id: str

    @property
    def enabled(self) -> bool: ...

    async def create_intent(self, *, deal_id: str, amount: int, currency: str, payer_address: str | None = None) -> PaymentIntent: ...

    async def verify_payment(self, intent: PaymentIntent) -> PaymentEvidence | None: ...

    async def verify_payout(self, intent: PaymentIntent, *, seller_address: str, query_id: int) -> PaymentEvidence | None: ...

    async def verify_refund(self, intent: PaymentIntent, *, buyer_address: str, query_id: int) -> PaymentEvidence | None: ...

    async def refund(self, evidence: PaymentEvidence) -> str: ...


class StarsPaymentRoute:
    """Reserved adapter. It is intentionally fail-closed and has no live API calls."""

    route_id = "stars"

    @property
    def enabled(self) -> bool:
        return False

    async def create_intent(self, *, deal_id: str, amount: int, currency: str) -> PaymentIntent:
        raise PaymentRouteError("Telegram Stars route is disabled pending legal/product approval")

    async def verify_payment(self, intent: PaymentIntent) -> PaymentEvidence | None:
        raise PaymentRouteError("Telegram Stars route is disabled")

    async def verify_payout(self, intent: PaymentIntent, *, seller_address: str,
                            query_id: int) -> PaymentEvidence | None:
        raise PaymentRouteError("Telegram Stars route is disabled")

    async def verify_refund(self, intent: PaymentIntent, *, buyer_address: str,
                            query_id: int) -> PaymentEvidence | None:
        raise PaymentRouteError("Telegram Stars route is disabled")

    async def refund(self, evidence: PaymentEvidence) -> str:
        raise PaymentRouteError("Telegram Stars route is disabled")


class TestnetTonUsdtGateway(Protocol):
    async def create_intent(self, *, deal_id: str, amount: int, currency: str,
                            escrow_address: str, jetton_master: str,
                            payer_address: str | None = None) -> PaymentIntent: ...

    async def verify_payment(self, intent: PaymentIntent) -> PaymentEvidence | None: ...

    async def verify_payout(self, intent: PaymentIntent, *, seller_address: str,
                            query_id: int) -> PaymentEvidence | None: ...

    async def verify_refund(self, intent: PaymentIntent, *, buyer_address: str,
                            query_id: int) -> PaymentEvidence | None: ...

    async def refund(self, evidence: PaymentEvidence) -> str: ...


class TonUsdtTestnetPaymentRoute:
    """Testnet route delegating chain observations/actions to an injected gateway.

    The gateway must use the testnet escrow and platform-issued test Jetton.
    No signer, private key, or seed phrase is accepted by this adapter.
    """

    route_id = "ton_usdt_testnet"

    def __init__(self, gateway: TestnetTonUsdtGateway, *, network: str,
                 escrow_address: str, jetton_master: str):
        if network != "testnet":
            raise PaymentRouteError("TON/USDT route is testnet-only")
        if not escrow_address or not jetton_master:
            raise PaymentRouteError("Testnet escrow address and test Jetton master are required")
        self._gateway = gateway
        self._escrow_address = escrow_address
        self._jetton_master = jetton_master

    @property
    def enabled(self) -> bool:
        return True

    @property
    def escrow_address(self) -> str:
        return self._escrow_address

    @property
    def jetton_master(self) -> str:
        return self._jetton_master

    async def create_intent(self, *, deal_id: str, amount: int, currency: str,
                            payer_address: str | None = None) -> PaymentIntent:
        if currency != "tUSDT":
            raise PaymentRouteError("Only the platform-issued tUSDT test Jetton is supported")
        if amount <= 0 or not deal_id:
            raise PaymentRouteError("A deal ID and positive amount are required")
        intent = await self._gateway.create_intent(
            deal_id=deal_id, amount=amount, currency=currency,
            escrow_address=self._escrow_address, jetton_master=self._jetton_master,
            payer_address=payer_address,
        )
        if payer_address and intent.payer_address != payer_address:
            raise PaymentRouteError("Gateway returned a payment intent for a different buyer wallet")
        return intent

    async def verify_payment(self, intent: PaymentIntent) -> PaymentEvidence | None:
        if intent.network != "testnet" or intent.currency != "tUSDT":
            raise PaymentRouteError("Payment intent does not belong to the TON testnet tUSDT route")
        evidence = await self._gateway.verify_payment(intent)
        if evidence is None:
            return None
        if (evidence.deal_id != intent.deal_id or evidence.reference != intent.reference
                or evidence.amount != intent.amount or evidence.currency != "tUSDT"
                or evidence.network != "testnet" or not evidence.finalized
                or not evidence.transaction_id):
            raise PaymentRouteError("Gateway returned incomplete or mismatched payment evidence")
        return evidence

    async def verify_payout(self, intent: PaymentIntent, *, seller_address: str,
                            query_id: int) -> PaymentEvidence | None:
        if intent.network != "testnet" or intent.currency != "tUSDT" or query_id <= 0:
            raise PaymentRouteError("Only testnet tUSDT payout intents with a positive query ID are supported")
        evidence = await self._gateway.verify_payout(
            intent, seller_address=seller_address, query_id=query_id,
        )
        if evidence is None:
            return None
        expected_reference = f"grpayout:{intent.deal_id}:{query_id}"
        if (evidence.deal_id != intent.deal_id or evidence.reference != expected_reference
                or evidence.amount != intent.amount or evidence.currency != "tUSDT"
                or evidence.network != "testnet" or not evidence.finalized
                or evidence.query_id != query_id or not evidence.transaction_id):
            raise PaymentRouteError("Gateway returned incomplete or mismatched seller payout evidence")
        return evidence

    async def verify_refund(self, intent: PaymentIntent, *, buyer_address: str,
                            query_id: int) -> PaymentEvidence | None:
        if intent.network != "testnet" or intent.currency != "tUSDT" or query_id <= 0:
            raise PaymentRouteError("Only testnet tUSDT refund intents with a positive query ID are supported")
        evidence = await self._gateway.verify_refund(
            intent, buyer_address=buyer_address, query_id=query_id,
        )
        if evidence is None:
            return None
        expected_reference = f"grrefund:{intent.deal_id}:{query_id}"
        if (evidence.deal_id != intent.deal_id or evidence.reference != expected_reference
                or evidence.amount != intent.amount or evidence.currency != "tUSDT"
                or evidence.network != "testnet" or not evidence.finalized
                or evidence.query_id != query_id or not evidence.transaction_id):
            raise PaymentRouteError("Gateway returned incomplete or mismatched buyer refund evidence")
        return evidence

    async def refund(self, evidence: PaymentEvidence) -> str:
        if evidence.network != "testnet" or evidence.currency != "tUSDT" or not evidence.finalized:
            raise PaymentRouteError("Only finalized testnet tUSDT evidence can enter refund flow")
        return await self._gateway.refund(evidence)


def configured_payment_route(*, ton_gateway: TestnetTonUsdtGateway | None = None) -> PaymentRoute:
    """Select a route by config without changing deal-core code.

    Defaults are deliberately the testnet route. Production/mainnet cannot be
    selected through this factory. Stars remains unavailable even if requested.
    """
    selected = os.getenv("PAYMENT_ROUTE", "ton_usdt_testnet").strip().lower()
    if selected == "stars":
        return StarsPaymentRoute()
    if selected != "ton_usdt_testnet":
        raise PaymentRouteError(f"Unsupported payment route: {selected}")
    if ton_gateway is None:
        from ton_testnet_gateway import configured_ton_testnet_gateway
        ton_gateway = configured_ton_testnet_gateway()
    return TonUsdtTestnetPaymentRoute(
        ton_gateway,
        network=os.getenv("TON_NETWORK", "testnet").strip().lower(),
        escrow_address=os.getenv("TON_TESTNET_ESCROW_ADDRESS", "").strip(),
        jetton_master=os.getenv("TON_TESTNET_USDT_MASTER", "").strip(),
    )
