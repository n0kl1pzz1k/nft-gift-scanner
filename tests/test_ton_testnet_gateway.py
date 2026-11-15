import asyncio
import os
import unittest
from unittest.mock import patch

from payment_routes import PaymentEvidence, PaymentIntent, PaymentRouteError
from ton_testnet_gateway import (
    TestnetTransfer, TonApiEmbedTestnetProvider, TonCenterTestnetProvider,
    TonTestnetPaymentGateway, configured_ton_testnet_gateway,
)


class Provider:
    def __init__(self, provider_id, transfer, payout=None):
        self.provider_id = provider_id
        self.transfer = transfer
        self.payout = payout
        self.wallet_addresses = {}

    async def get_jetton_wallet_address(self, *, owner_address, jetton_master):
        return self.wallet_addresses.get(owner_address)

    async def find_transfer(self, *, escrow_address, jetton_master, reference):
        return self.transfer

    async def find_payout(self, *, escrow_address, jetton_master, recipient_address, query_id, amount, reference):
        return self.payout


class TonTestnetGatewayTests(unittest.TestCase):
    def setUp(self):
        self.intent = PaymentIntent(
            deal_id="deal-123", amount=2500000, currency="tUSDT",
            reference="grpay:deal-123", network="testnet",
        )
        self.seller_address = "0:" + "44" * 32
        self.buyer_address = "0:" + "66" * 32
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

    def refund_transfer(self, provider_id):
        return TestnetTransfer(
            provider_id=provider_id, transaction_id="cd" * 32,
            deal_id="deal-123", reference="grrefund:deal-123:993",
            amount=2500000, currency="tUSDT", network="testnet",
            sender="0:" + "22" * 32, recipient=self.buyer_address,
            finalized=True, query_id=993,
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
            escrow_jetton_wallet_address="0:" + "55" * 32,
        )


    def test_create_intent_requires_the_configured_escrow_jetton_wallet(self):
        gateway = self.gateway()
        center = gateway.providers[0]
        center.wallet_addresses = {
            gateway.escrow_address: "0:" + "55" * 32,
            self.buyer_address: "0:" + "77" * 32,
        }
        intent = asyncio.run(gateway.create_intent(
            deal_id="deal-123", amount=2500000, currency="tUSDT",
            escrow_address=gateway.escrow_address,
            jetton_master=gateway.jetton_master,
            payer_address=self.buyer_address,
        ))
        self.assertEqual(intent.escrow_jetton_wallet_address, "0:" + "55" * 32)
        self.assertEqual(intent.payer_jetton_wallet_address, "0:" + "77" * 32)

        center.wallet_addresses[gateway.escrow_address] = "0:" + "88" * 32
        with self.assertRaises(PaymentRouteError):
            asyncio.run(gateway.create_intent(
                deal_id="deal-123", amount=2500000, currency="tUSDT",
                escrow_address=gateway.escrow_address,
                jetton_master=gateway.jetton_master,
                payer_address=self.buyer_address,
            ))

    def test_providers_reject_mainnet_and_non_testnet_hosts(self):
        with self.assertRaises(PaymentRouteError):
            TonCenterTestnetProvider(base_url="https://toncenter.com/api/v3")
        with self.assertRaises(PaymentRouteError):
            TonCenterTestnetProvider(base_url="https://example.com/testnet")
        with self.assertRaises(PaymentRouteError):
            TonApiEmbedTestnetProvider(base_url="https://tonapi.io/v2")
        with self.assertRaises(PaymentRouteError):
            TonApiEmbedTestnetProvider(base_url="http://testnet.tonapi.io/v2")

    def test_provider_rejects_lookalike_testnet_domains(self):
        for url in (
            "https://testnet.toncenter.com.evil.example/api/v3",
            "https://testnet.evil.example/api/v3",
            "https://evil.testnet.toncenter.com/api/v3",
            "https://testnet.tonapi.io.evil.example/v2",
            "https://testnet.evil.example/v2",
            "https://evil.testnet.tonapi.io/v2",
        ):
            with self.subTest(url=url):
                provider = TonCenterTestnetProvider if "toncenter" in url else TonApiEmbedTestnetProvider
                with self.assertRaises(PaymentRouteError):
                    provider(base_url=url)

    def test_toncenter_resolves_only_matching_jetton_wallet(self):
        owner = "0:" + "11" * 32
        master = "0:" + "22" * 32

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"jetton_wallets": [
                    {"address": "0:" + "33" * 32, "owner": "0:" + "44" * 32, "jetton": master},
                    {"address": "0:" + "55" * 32, "owner": owner, "jetton": master},
                ]}

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, *args, **kwargs):
                return Response()

        async def run():
            provider = TonCenterTestnetProvider(base_url="https://testnet.toncenter.com/api/v3")
            with patch("ton_testnet_gateway.httpx.AsyncClient", Client):
                return await provider.get_jetton_wallet_address(
                    owner_address=owner, jetton_master=master,
                )

        self.assertEqual(asyncio.run(run()), "0:" + "55" * 32)

    def test_gateway_uses_public_tonapi_testnet_endpoint_and_pinned_wallet(self):
        env = {
            "TON_TESTNET_ESCROW_ADDRESS": "0:" + "22" * 32,
            "TON_TESTNET_USDT_MASTER": "0:" + "33" * 32,
            "TON_TESTNET_ESCROW_JETTON_WALLET": "0:" + "55" * 32,
            "TON_TESTNET_TONAPI_KEY": "test-tonapi-key",
        }
        with patch.dict(os.environ, env, clear=True):
            gateway = configured_ton_testnet_gateway()
        tonapi = next(provider for provider in gateway.providers if provider.provider_id == "tonapi")
        self.assertEqual(tonapi.base_url, "https://testnet.tonapi.io/v2")
        self.assertEqual(tonapi.api_key, "test-tonapi-key")

    def test_tonapi_provider_accepts_only_the_official_testnet_api(self):
        provider = TonApiEmbedTestnetProvider(
            base_url="https://testnet.tonapi.io/v2",
        )
        self.assertEqual(provider.provider_id, "tonapi")
        for url in (
            "https://testnet.tonapi.io.evil.example/v2",
            "https://testnet.evil.example/v2",
            "https://tonapi.io/v2",
        ):
            with self.subTest(url=url), self.assertRaises(PaymentRouteError):
                TonApiEmbedTestnetProvider(base_url=url)

    def test_providers_accept_https_testnet_hosts(self):
        center = TonCenterTestnetProvider(base_url="https://testnet.toncenter.com/api/v3")
        tonapi = TonApiEmbedTestnetProvider(base_url="https://testnet.tonapi.io/v2")
        self.assertEqual(center.provider_id, "toncenter")
        self.assertEqual(tonapi.provider_id, "tonapi")

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

    def test_matching_finalized_buyer_refund_requires_two_providers(self):
        result = asyncio.run(self.gateway(
            payout1=self.refund_transfer("toncenter"),
            payout2=self.refund_transfer("tonapi"),
        ).verify_refund(
            self.intent, buyer_address=self.buyer_address, query_id=993,
        ))
        self.assertIsInstance(result, PaymentEvidence)
        self.assertEqual(result.reference, "grrefund:deal-123:993")
        self.assertEqual(result.query_id, 993)

    def test_buyer_refund_disagreement_or_wrong_recipient_fails_closed(self):
        other = TestnetTransfer(**{
            **self.refund_transfer("tonapi").__dict__,
            "transaction_id": "aa" * 32,
        })
        with self.assertRaises(PaymentRouteError):
            asyncio.run(self.gateway(
                payout1=self.refund_transfer("toncenter"), payout2=other,
            ).verify_refund(
                self.intent, buyer_address=self.buyer_address, query_id=993,
            ))
        wrong = TestnetTransfer(**{
            **self.refund_transfer("tonapi").__dict__,
            "recipient": self.seller_address,
        })
        self.assertIsNone(asyncio.run(self.gateway(
            payout1=wrong, payout2=wrong,
        ).verify_refund(
            self.intent, buyer_address=self.buyer_address, query_id=993,
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
