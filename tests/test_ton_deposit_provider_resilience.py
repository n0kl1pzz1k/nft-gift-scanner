import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, insert
from financial_ledger import get_account_balance, init_financial_schema, invoices
from ton_deposit_monitor import scan_ton_invoice


class FakeProvider:
    def __init__(self, provider_id, trace=None, error=None):
        self.provider_id = provider_id
        self.trace = trace
        self.error = error

    async def find_invoice_transfer(self, invoice):
        if self.error:
            raise self.error
        return self.trace


class TonDepositProviderResilienceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.temp.name) / 'provider-resilience.sqlite3'}",
            future=True,
        )
        init_financial_schema(self.engine)
        self.invoice = {
            "invoice_id": "gri_resilience", "user_id": "501", "asset_id": "GRAM",
            "chain": "ton-mainnet", "master_address": "", "asset_kind": "native",
            "recipient_address": "EQmarket", "amount_raw": "500000000",
            "payload": "gift-radar-invoice", "expires_at": int(time.time()) + 600,
            "status": "pending", "created_at": int(time.time()),
        }
        with self.engine.begin() as conn:
            conn.execute(insert(invoices).values(**self.invoice))
        self.trace = {
            "chain": "ton-mainnet", "asset_kind": "native",
            "sender_address": "EQseller", "recipient_address": "EQmarket",
            "amount_raw": 500000000, "payload": "gift-radar-invoice",
            "successful": True, "bounced": False, "finalized": True,
            "tx_hash": "ton-resilience-event", "event_index": 0,
        }

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    async def test_one_provider_outage_does_not_skip_the_other_or_credit(self):
        providers = [
            FakeProvider("toncenter", trace=self.trace),
            FakeProvider("tonapi", error=TimeoutError("provider unavailable")),
        ]
        with patch.dict(os.environ, {"TON_DEPOSIT_PROVIDER_IDS": "toncenter,tonapi"}):
            result = await scan_ton_invoice(
                invoice_id="gri_resilience", providers=providers, engine=self.engine
            )
        self.assertFalse(result["settled"])
        self.assertEqual(result["observations"], 1)
        self.assertEqual(result["provider_count"], 1)
        self.assertEqual(result["provider_error_count"], 1)
        self.assertEqual(result["provider_errors"][0]["provider_id"], "tonapi")
        self.assertEqual(
            get_account_balance("liability:user:501:GRAM", "GRAM", self.engine), 0
        )

    async def test_two_healthy_providers_still_settle_once(self):
        providers = [
            FakeProvider("toncenter", trace=self.trace),
            FakeProvider("tonapi", trace=self.trace),
        ]
        with patch.dict(os.environ, {"TON_DEPOSIT_PROVIDER_IDS": "toncenter,tonapi"}):
            result = await scan_ton_invoice(
                invoice_id="gri_resilience", providers=providers, engine=self.engine
            )
        self.assertTrue(result["settled"])
        self.assertEqual(result["provider_error_count"], 0)
        self.assertEqual(
            get_account_balance("liability:user:501:GRAM", "GRAM", self.engine),
            -500000000,
        )


if __name__ == "__main__":
    unittest.main()
