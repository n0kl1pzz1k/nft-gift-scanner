import asyncio
import unittest

from payment_routes import PaymentEvidence, PaymentIntent, PaymentRouteError
from ton_testnet_gateway import TestnetTransfer, TonTestnetPaymentGateway


class Provider:
    def __init__(self, provider_id, transfer):
        self.provider_id = provider_id
        self.transfer = transfer

    async def find_transfer(self, *, escrow_address, jetton_master, reference):
        return self.transfer


class TonTestnetGatewayTests(unittest.TestCase):
    def setUp(self):
        self.intent = PaymentIntent(
            deal_id="deal-123", amount=2500000, currency="tUSDT",
            reference="grpay:deal-123", network="testnet",
        )
        self.transfer = TestnetTransfer(
            provider_id="toncenter", transaction_id="ab" * 32,
            deal_id="deal-123", reference="grpay:deal-123",
            amount=2500000, currency="tUSDT", network="testnet",
            sender="0:" + "11" * 32, recipient="0:" + "22" * 32,
            finalized=True, query_id=991,
        )

    def gateway(self, first=None, second=None):
        second_transfer = second or TestnetTransfer(
            **{**self.transfer.__dict__, "provider_id": "tonapi"}
        )
        return TonTestnetPaymentGateway(
            providers=[Provider("toncenter", first or self.transfer),
                       Provider("tonapi", second_transfer)],
            escrow_address="0:" + "22" * 32,
            jetton_master="0:" + "33" * 32,
        )

    def test_requires_two_distinct_independent_providers(self):
        with self.assertRaises(PaymentRouteError):
            TonTestnetPaymentGateway(
                providers=[Provider("toncenter", self.transfer)],
                escrow_address="escrow", jetton_master="master",
            )

    def test_matching_finalized_provider_evidence_confirms_payment(self):
        result = asyncio.run(self.gateway().verify_payment(self.intent))
        self.assertIsInstance(result, PaymentEvidence)
        self.assertEqual(result.transaction_id, "ab" * 32)
        self.assertEqual(result.query_id, 991)
        self.assertTrue(result.finalized)

    def test_provider_disagreement_fails_closed(self):
        other = TestnetTransfer(**{**self.transfer.__dict__,
            "provider_id": "tonapi", "transaction_id": "cd" * 32})
        with self.assertRaises(PaymentRouteError):
            asyncio.run(self.gateway(second=other).verify_payment(self.intent))

    def test_unfinalized_or_missing_query_id_never_confirms(self):
        unfinalized = TestnetTransfer(**{**self.transfer.__dict__,
            "provider_id": "toncenter", "finalized": False})
        other = TestnetTransfer(**{**unfinalized.__dict__, "provider_id": "tonapi"})
        self.assertIsNone(asyncio.run(self.gateway(unfinalized, other).verify_payment(self.intent)))
        missing = TestnetTransfer(**{**self.transfer.__dict__,
            "provider_id": "toncenter", "query_id": 0})
        other_missing = TestnetTransfer(**{**missing.__dict__, "provider_id": "tonapi"})
        self.assertIsNone(asyncio.run(self.gateway(missing, other_missing).verify_payment(self.intent)))


if __name__ == "__main__":
    unittest.main()
