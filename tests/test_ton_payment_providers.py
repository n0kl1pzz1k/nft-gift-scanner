import unittest

from ton_payment_providers import TonApiPaymentProvider


def address(byte):
    return "0:" + (byte * 64)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("fake HTTP failure")

    def json(self):
        return self.payload


class FakeAsyncClient:
    def __init__(self, routes):
        self.routes = routes

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None):
        prefix = "https://tonapi.io/v2"
        path = url.removeprefix(prefix)
        return FakeResponse(self.routes.get(path, {}))


class TonApiMainnetProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.master = address("1")
        self.buyer = address("2")
        self.escrow = address("3")
        self.buyer_wallet = address("4")
        self.escrow_wallet = address("5")
        self.other_wallet = address("6")
        self.tx_hash = "ab" * 32
        self.invoice = {
            "chain": "ton-mainnet",
            "asset_kind": "jetton",
            "master_address": self.master,
            "recipient_address": self.escrow,
            "created_at": 1_700_000_000,
            "amount_raw": 1_250_000,
            "payload": "gri_invoice_123",
            "query_id": 42,
        }
        self.routes = {
            "/status": {
                "rest_online": True,
                "last_known_masterchain_seqno": 50_000_000,
            },
            f"/jettons/{self.master}/accounts/{self.escrow}/history": {
                "operations": [{
                    "operation": "transfer",
                    "utime": 1_700_000_100,
                    "lt": 777,
                    "transaction_hash": self.tx_hash,
                    "source": {"address": self.buyer},
                    "destination": {"address": self.escrow},
                    "amount": "1250000",
                    "jetton": {"address": self.master},
                    "trace_id": self.tx_hash,
                    "query_id": "42",
                }],
                "next_from": 0,
            },
            f"/accounts/{self.escrow}/jettons/{self.master}": {
                "wallet_address": self.escrow_wallet,
            },
            f"/accounts/{self.buyer}/jettons/{self.master}": {
                "wallet_address": self.buyer_wallet,
            },
            f"/blockchain/transactions/{self.tx_hash}": {
                "hash": self.tx_hash,
                "lt": 777,
                "account": {"address": self.escrow_wallet},
                "success": True,
                "aborted": False,
                "block": "(0,50000000,8000000000000000)",
                "compute_phase": {"success": True, "exit_code": 0},
                "action_phase": {"success": True, "result_code": 0},
                "in_msg": {
                    "msg_type": "int_msg",
                    "source": {"address": self.buyer_wallet},
                    "destination": {"address": self.escrow_wallet},
                    "op_code": "0x7362d09c",
                    "bounced": False,
                    "decoded_body": {
                        "query_id": "42",
                        "amount": "1250000",
                        "sender": {"address": self.buyer},
                        "forward_payload": {"text": "gri_invoice_123"},
                    },
                },
            },
        }
        self.provider = TonApiPaymentProvider(api_key="test-api-key")
        self.provider._client = lambda: FakeAsyncClient(self.routes)

    async def test_uses_official_rest_and_checks_both_jetton_wallets(self):
        traces = await self.provider.find_invoice_transfers(self.invoice)
        self.assertEqual(len(traces), 1)
        trace = traces[0]
        self.assertEqual(trace["provider_id"] if "provider_id" in trace else "tonapi", "tonapi")
        self.assertEqual(trace["master_address"], self.master)
        self.assertEqual(trace["sender_address"], self.buyer)
        self.assertEqual(trace["recipient_address"], self.escrow)
        self.assertEqual(trace["source_jetton_wallet"], self.buyer_wallet)
        self.assertEqual(trace["expected_source_jetton_wallet"], self.buyer_wallet)
        self.assertEqual(trace["destination_jetton_wallet"], self.escrow_wallet)
        self.assertEqual(trace["expected_destination_jetton_wallet"], self.escrow_wallet)
        self.assertEqual(trace["amount_raw"], 1_250_000)
        self.assertEqual(trace["payload"], "gri_invoice_123")
        self.assertTrue(trace["successful"])
        self.assertTrue(trace["finalized"])

    async def test_rejects_a_transaction_from_another_source_jetton_wallet(self):
        self.routes[f"/blockchain/transactions/{self.tx_hash}"]["in_msg"]["source"] = {
            "address": self.other_wallet,
        }
        self.assertEqual(await self.provider.find_invoice_transfers(self.invoice), [])

    async def test_rejects_a_transaction_to_another_destination_jetton_wallet(self):
        self.routes[f"/blockchain/transactions/{self.tx_hash}"]["account"] = {
            "address": self.other_wallet,
        }
        self.assertEqual(await self.provider.find_invoice_transfers(self.invoice), [])

    async def test_rejects_failed_or_bounced_transaction(self):
        self.routes[f"/blockchain/transactions/{self.tx_hash}"]["aborted"] = True
        self.assertEqual(await self.provider.find_invoice_transfers(self.invoice), [])

    async def test_rejects_wrong_master_and_query_id(self):
        self.routes[f"/jettons/{self.master}/accounts/{self.escrow}/history"]["operations"][0]["jetton"] = {
            "address": self.other_wallet,
        }
        self.assertEqual(await self.provider.find_invoice_transfers(self.invoice), [])
        self.routes[f"/jettons/{self.master}/accounts/{self.escrow}/history"]["operations"][0]["jetton"] = {
            "address": self.master,
        }
        self.routes[f"/jettons/{self.master}/accounts/{self.escrow}/history"]["operations"][0]["query_id"] = "99"
        self.assertEqual(await self.provider.find_invoice_transfers(self.invoice), [])

    def test_requires_a_dedicated_api_key(self):
        with self.assertRaises(RuntimeError):
            TonApiPaymentProvider(api_key="")


if __name__ == "__main__":
    unittest.main()
