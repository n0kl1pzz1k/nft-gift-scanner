"""Telegram Stars is the sole marketplace checkout route.

TON Connect is a wallet protocol for TON ownership proofs and user-approved
NFT transactions. It is deliberately not exposed as an in-Mini-App checkout.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class PaymentRouteError(RuntimeError):
    """Raised when the only supported payment route cannot safely proceed."""


@dataclass(frozen=True)
class PaymentIntent:
    deal_id: str
    amount: int
    currency: str
    reference: str
    network: str = "telegram"


@dataclass(frozen=True)
class PaymentEvidence:
    deal_id: str
    reference: str
    amount: int
    currency: str
    transaction_id: str
    finalized: bool
    network: str = "telegram"


@runtime_checkable
class PaymentRoute(Protocol):
    route_id: str

    @property
    def enabled(self) -> bool: ...

    async def create_intent(self, *, deal_id: str, amount: int, currency: str) -> PaymentIntent: ...

    async def verify_payment(self, intent: PaymentIntent) -> PaymentEvidence | None: ...


class StarsPaymentRoute:
    """Stars adapter boundary. Actual confirmation remains Telegram-webhook-only."""

    route_id = "stars"

    @property
    def enabled(self) -> bool:
        return False

    async def create_intent(self, *, deal_id: str, amount: int, currency: str) -> PaymentIntent:
        raise PaymentRouteError("Use the authenticated Telegram Stars invoice flow")

    async def verify_payment(self, intent: PaymentIntent) -> PaymentEvidence | None:
        raise PaymentRouteError("Stars payment evidence is accepted only from Telegram successful_payment")


def configured_payment_route() -> PaymentRoute:
    """Return the sole supported route; no environment variable can select crypto."""
    return StarsPaymentRoute()
