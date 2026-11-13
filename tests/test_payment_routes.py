from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from payment_routes import (
    PaymentEvidence,
    PaymentIntent,
    PaymentRouteError,
    StarsPaymentRoute,
    TonUsdtTestnetPaymentRoute,
    configured_payment_route,
)


class FakeGateway:
    def __init__(self):
        self.evidence = None
        self.intent_args = None

    async def create_intent(self, **kwargs):
        self.intent_args = kwargs
        return PaymentIntent(
            deal_id=kwargs["deal_id"], amount=kwargs["amount"],
            currency=kwargs["currency"], reference="grpay_test_1", network="testnet",
        )

    async def verify_payment(self, intent):
        return self.evidence

    async def refund(self, evidence):
        return "testnet-refund-request"


class PaymentRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_stars_is_always_disabled_and_fails_closed(self):
        route = StarsPaymentRoute()
        self.assertFalse(route.enabled)
        with self.assertRaises(PaymentRouteError):
            await route.create_intent(deal_id="d1", amount=100, currency="XTR")

    async def test_testnet_route_builds_intent_for_configured_escrow_and_test_jetton(self):
        gateway = FakeGateway()
        route = TonUsdtTestnetPaymentRoute(
            gateway, network="testnet", escrow_address="EQ_ESCROW_TEST",
            jetton_master="EQ_TEST_JETTON",
        )
        intent = await route.create_intent(deal_id="deal-1", amount=500, currency="tUSDT")
        self.assertEqual(intent.network, "testnet")
        self.assertEqual(gateway.intent_args["escrow_address"], "EQ_ESCROW_TEST")
        self.assertEqual(gateway.intent_args["jetton_master"], "EQ_TEST_JETTON")

    async def test_mainnet_and_real_usdt_are_rejected(self):
        gateway = FakeGateway()
        with self.assertRaises(PaymentRouteError):
            TonUsdtTestnetPaymentRoute(
                gateway, network="mainnet", escrow_address="EQ_ESCROW",
                jetton_master="EQ_MASTER",
            )
        route = TonUsdtTestnetPaymentRoute(
            gateway, network="testnet", escrow_address="EQ_ESCROW",
            jetton_master="EQ_MASTER",
        )
        with self.assertRaises(PaymentRouteError):
            await route.create_intent(deal_id="deal-1", amount=1, currency="USDT")

    async def test_payment_evidence_must_match_intent_and_be_finalized(self):
        gateway = FakeGateway()
        route = TonUsdtTestnetPaymentRoute(
            gateway, network="testnet", escrow_address="EQ_ESCROW",
            jetton_master="EQ_MASTER",
        )
        intent = await route.create_intent(deal_id="deal-1", amount=500, currency="tUSDT")
        gateway.evidence = PaymentEvidence(
            deal_id="deal-1", reference=intent.reference, amount=500,
            currency="tUSDT", transaction_id="tx-test-1", finalized=True,
            network="testnet",
        )
        self.assertEqual((await route.verify_payment(intent)).transaction_id, "tx-test-1")
        gateway.evidence = PaymentEvidence(
            deal_id="deal-1", reference=intent.reference, amount=500,
            currency="tUSDT", transaction_id="tx-test-2", finalized=False,
            network="testnet",
        )
        with self.assertRaises(PaymentRouteError):
            await route.verify_payment(intent)

    async def test_config_selects_route_without_core_changes_and_is_fail_closed(self):
        gateway = FakeGateway()
        env = {
            "PAYMENT_ROUTE": "ton_usdt_testnet",
            "TON_NETWORK": "testnet",
            "TON_TESTNET_ESCROW_ADDRESS": "EQ_ESCROW",
            "TON_TESTNET_USDT_MASTER": "EQ_MASTER",
        }
        with patch.dict(os.environ, env, clear=False):
            route = configured_payment_route(ton_gateway=gateway)
            self.assertEqual(route.route_id, "ton_usdt_testnet")
        with patch.dict(os.environ, {"PAYMENT_ROUTE": "unknown"}, clear=False):
            with self.assertRaises(PaymentRouteError):
                configured_payment_route(ton_gateway=gateway)
        with patch.dict(os.environ, {"PAYMENT_ROUTE": "ton_usdt_testnet",
                                     "TON_NETWORK": "mainnet"}, clear=False):
            with self.assertRaises(PaymentRouteError):
                configured_payment_route(ton_gateway=gateway)


if __name__ == "__main__":
    unittest.main()
