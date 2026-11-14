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
        self.payout_evidence = None
        self.refund_evidence = None

    async def create_intent(self, **kwargs):
        self.intent_args = kwargs
        return PaymentIntent(
            deal_id=kwargs["deal_id"], amount=kwargs["amount"],
            currency=kwargs["currency"], reference="grpay_test_1", network="testnet",
            payer_address=kwargs.get("payer_address"),
        )

    async def verify_payment(self, intent):
        return self.evidence

    async def verify_payout(self, intent, *, seller_address, query_id):
        return self.payout_evidence

    async def verify_refund(self, intent, *, buyer_address, query_id):
        return self.refund_evidence

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
        payer = "0:" + "77" * 32
        intent = await route.create_intent(
            deal_id="deal-1", amount=500, currency="tUSDT", payer_address=payer,
        )
        self.assertEqual(intent.network, "testnet")
        self.assertEqual(intent.payer_address, payer)
        self.assertEqual(gateway.intent_args["escrow_address"], "EQ_ESCROW_TEST")
        self.assertEqual(gateway.intent_args["jetton_master"], "EQ_TEST_JETTON")

    async def test_payout_evidence_must_match_seller_and_query_id(self):
        gateway = FakeGateway()
        route = TonUsdtTestnetPaymentRoute(
            gateway, network="testnet", escrow_address="EQ_ESCROW",
            jetton_master="EQ_MASTER",
        )
        intent = await route.create_intent(deal_id="deal-1", amount=500, currency="tUSDT")
        gateway.payout_evidence = PaymentEvidence(
            deal_id="deal-1", reference="grpayout:deal-1:77", amount=500,
            currency="tUSDT", transaction_id="payout-tx", finalized=True,
            network="testnet", query_id=77,
        )
        result = await route.verify_payout(intent, seller_address="EQ_SELLER", query_id=77)
        self.assertEqual(result.transaction_id, "payout-tx")
        gateway.payout_evidence = PaymentEvidence(
            deal_id="deal-1", reference="grpayout:deal-1:78", amount=500,
            currency="tUSDT", transaction_id="wrong-query", finalized=True,
            network="testnet", query_id=78,
        )
        with self.assertRaises(PaymentRouteError):
            await route.verify_payout(intent, seller_address="EQ_SELLER", query_id=77)

    async def test_refund_evidence_must_match_buyer_and_query_id(self):
        gateway = FakeGateway()
        route = TonUsdtTestnetPaymentRoute(
            gateway, network="testnet", escrow_address="EQ_ESCROW",
            jetton_master="EQ_MASTER",
        )
        intent = await route.create_intent(deal_id="deal-1", amount=500, currency="tUSDT")
        self.assertEqual(route.escrow_address, "EQ_ESCROW")
        self.assertEqual(route.jetton_master, "EQ_MASTER")
        gateway.refund_evidence = PaymentEvidence(
            deal_id="deal-1", reference="grrefund:deal-1:88", amount=500,
            currency="tUSDT", transaction_id="refund-tx", finalized=True,
            network="testnet", query_id=88,
        )
        result = await route.verify_refund(intent, buyer_address="EQ_BUYER", query_id=88)
        self.assertEqual(result.transaction_id, "refund-tx")
        gateway.refund_evidence = PaymentEvidence(
            deal_id="deal-1", reference="grrefund:deal-1:89", amount=500,
            currency="tUSDT", transaction_id="wrong-refund", finalized=True,
            network="testnet", query_id=89,
        )
        with self.assertRaises(PaymentRouteError):
            await route.verify_refund(intent, buyer_address="EQ_BUYER", query_id=88)

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
