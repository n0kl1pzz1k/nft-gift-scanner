import asyncio
import unittest

from payment_routes import PaymentEvidence, PaymentIntent, PaymentRouteError
from ton_testnet_gateway import TestnetTransfer, TonTestnetPaymentGateway


class Provider:
    def __init__(self, provider_id, transfer, payout=None):
        self.provider_id = provider_id
        self.transfer = transfer
        self.payout = payout

    async def find_transfer(self, *, escrow_address, jetton_master, reference):
        return self.transfer

    async def find_payout(self, *, escrow_address, jetton_master, seller_address, query_id, amount, reference):
        return self.payout


class TonTestnetGatewayTests(unittest.TestCase):
    def setUp(self):
        self.intent = PaymentIntent(
            deal_id="deal-123", amount=2500000, currency="tUSDT",
            reference="grpay:deal-123", network="testnet",
        )
        self.seller_address = "0:" + "44" * 32
        self.transfer = TestnetTransfer(
            provider_id="toncenter", transaction_id="ab" * 32,
            deal_id="deal-123", reference="grpay:deal-123",
            amount=2500000, currency="tUSDT", network="testnet",
            sender="0:" + "11" * 32, recipient="0:" + "22" * 32,
            finalized=True, query_id=991,
        )

    def payout_transfer(self, provider_id):
        return TestnetTransfer(
            provider_id=provider_id, transaction_id="ef" * 32,
            deal_id="deal-123", reference="grpayout:deal-123:992",
            amount=2500000, currency="tUSDT", network="testnet",
            sender="0:" + "22" * 32, recipient=self.seller_address,
            finalized=True, query_id=992,
        )

    def gateway(self, first=None, second=None, payout1=None, payout2=None):
        second_transfer = second or TestnetTransfer(
            **{**self.transfer.__dict__, "provider_id": "tonapi", "query_id": 0}
        )
        return TonTestnetPaymentGateway(
            providers=[Provider("toncenter", first or self.transfer, payout1 or self.payout_transfer("toncenter")),
                       Provider("tonapi", second_transfer, payout2 or self.payout_transfer("tonapi"))],
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

    def test_matching_finalized_seller_payout_requires_two_providers(self):
        result = asyncio.run(self.gateway().verify_payout(
            self.intent, seller_address=self.seller_address, query_id=992,
        ))
        self.assertIsInstance(result, PaymentEvidence)
        self.assertEqual(result.reference, "grpayout:deal-123:992")
        self.assertEqual(result.query_id, 992)

    def test_seller_payout_disagreement_or_wrong_recipient_fails_closed(self):
        other = TestnetTransfer(**{
            **self.payout_transfer("tonapi").__dict__,
            "transaction_id": "aa" * 32,
        })
        with self.assertRaises(PaymentRouteError):
            asyncio.run(self.gateway(payout2=other).verify_payout(
                self.intent, seller_address=self.seller_address, query_id=992,
            ))
        wrong = TestnetTransfer(**{
            **self.payout_transfer("tonapi").__dict__,
            "recipient": "0:" + "55" * 32,
        })
        self.assertIsNone(asyncio.run(self.gateway(payout1=wrong, payout2=wrong).verify_payout(
            self.intent, seller_address=self.seller_address, query_id=992,
        )))

    def test_payment_from_wrong_buyer_wallet_does_not_confirm(self):
        expected = PaymentIntent(
            deal_id="deal-123", amount=2500000, currency="tUSDT",
            reference="grpay:deal-123", network="testnet",
            payer_address="0:" + "99" * 32,
        )
        self.assertIsNone(asyncio.run(self.gateway().verify_payment(expected)))

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
        other_missing = TestnetTransfer(**{**missing.__dict__, "provider_id": "tonapi", "query_id": 0})
        self.assertIsNone(asyncio.run(self.gateway(missing, other_missing).verify_payment(self.intent)))


if __name__ == "__main__":
    unittest.main()
