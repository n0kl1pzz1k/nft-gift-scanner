import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, insert, select
from sqlalchemy.pool import StaticPool

from financial_ledger import get_account_balance, init_financial_schema, invoices
from ton_deposit_monitor import record_ton_provider_observation


class TonDepositMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.temp.name) / 'ton-monitor.sqlite3'}", future=True
        )
        init_financial_schema(self.engine)
        self.invoice = {
            "invoice_id": "gri_monitor", "user_id": "501", "asset_id": "GRAM",
            "chain": "ton-mainnet", "master_address": "", "asset_kind": "native",
            "recipient_address": "EQmarket", "amount_raw": "500000000",
            "payload": "gift-radar-invoice", "expires_at": int(time.time()) + 600,
            "status": "pending", "created_at": int(time.time()),
        }
        with self.engine.begin() as conn:
            conn.execute(insert(invoices).values(**self.invoice))

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def trace(self, sender="EQseller"):
        return {
            "chain": "ton-mainnet", "asset_kind": "native", "sender_address": sender,
            "recipient_address": "EQmarket", "amount_raw": 500000000,
            "payload": "gift-radar-invoice", "successful": True, "bounced": False,
            "finalized": True, "tx_hash": "ton-tx-unique", "event_index": 0,
        }

    def test_two_independent_matching_providers_credit_once(self):
        with patch.dict(os.environ, {"TON_DEPOSIT_PROVIDER_IDS": "toncenter,tonapi"}):
            first = record_ton_provider_observation(
                invoice_id="gri_monitor", provider_id="toncenter",
                trace=self.trace(), engine=self.engine
            )
            self.assertFalse(first["settled"])
            second = record_ton_provider_observation(
                invoice_id="gri_monitor", provider_id="tonapi",
                trace=self.trace(), engine=self.engine
            )
            self.assertTrue(second["settled"])
            replay = record_ton_provider_observation(
                invoice_id="gri_monitor", provider_id="tonapi",
                trace=self.trace(), engine=self.engine
            )
            self.assertTrue(replay["duplicate"])
            self.assertTrue(replay["duplicate"] or replay["settled"])
        self.assertEqual(
            get_account_balance("liability:user:501:GRAM", "GRAM", self.engine),
            -500000000
        )

    def test_provider_disagreement_never_credits(self):
        with patch.dict(os.environ, {"TON_DEPOSIT_PROVIDER_IDS": "toncenter,tonapi"}):
            record_ton_provider_observation(
                invoice_id="gri_monitor", provider_id="toncenter",
                trace=self.trace("EQsellerA"), engine=self.engine
            )
            result = record_ton_provider_observation(
                invoice_id="gri_monitor", provider_id="tonapi",
                trace=self.trace("EQsellerB"), engine=self.engine
            )
        self.assertTrue(result["review_required"])
        self.assertFalse(result["settled"])
        self.assertEqual(
            get_account_balance("liability:user:501:GRAM", "GRAM", self.engine), 0
        )

    def test_late_confirmed_payment_is_quarantined_without_credit(self):
        with patch.dict(os.environ, {"TON_DEPOSIT_PROVIDER_IDS": "toncenter,tonapi"}):
            engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False},
                                   poolclass=StaticPool)
            init_financial_schema(engine)
            invoice = {
                "invoice_id": "gri_late_payment", "user_id": "42", "asset_id": "usdt-ton",
                "chain": "ton-mainnet", "master_address": "master",
                "recipient_address": "wallet", "amount_raw": "100000",
                "payload": "gri_late_payment", "expires_at": 900,
                "status": "pending", "created_at": 100,
            }
            with engine.begin() as conn:
                conn.execute(insert(invoices).values(**invoice))
            trace = {
                "chain": "ton-mainnet", "asset_kind": "jetton",
                "master_address": "master", "source_jetton_wallet": "source-wallet",
                "expected_source_jetton_wallet": "source-wallet",
                "destination_jetton_wallet": "destination-wallet",
                "expected_destination_jetton_wallet": "destination-wallet",
                "sender_address": "sender", "recipient_address": "wallet",
                "amount_raw": 100000, "payload": "gri_late_payment",
                "opcode": "0x7362d09c", "successful": True, "bounced": False,
                "finalized": True, "tx_hash": "a" * 64, "event_index": 12,
            }
            first = record_ton_provider_observation(
                invoice_id="gri_late_payment", provider_id="toncenter",
                trace=trace, engine=engine, now=1000,
            )
            self.assertFalse(first["settled"])
            second = record_ton_provider_observation(
                invoice_id="gri_late_payment", provider_id="tonapi",
                trace=trace, engine=engine, now=1000,
            )
            self.assertFalse(second["settled"])
            self.assertTrue(second["review_required"])
            self.assertEqual(second["reason"], "late_payment_after_invoice_expiry")
            replay = record_ton_provider_observation(
                invoice_id="gri_late_payment", provider_id="tonapi",
                trace=trace, engine=engine, now=1001,
            )
            self.assertTrue(replay["review_required"])
            self.assertEqual(get_account_balance(
                "liability:user:42:usdt-ton", "usdt-ton", engine
            ), 0)
            with engine.connect() as conn:
                status = conn.execute(select(invoices.c.status).where(
                    invoices.c.invoice_id == "gri_late_payment"
                )).scalar_one()
            self.assertEqual(status, "reconciliation_required")
            engine.dispose()

    def test_single_or_unconfigured_provider_fails_closed(self):
        with patch.dict(os.environ, {"TON_DEPOSIT_PROVIDER_IDS": "toncenter"}):
            with self.assertRaises(RuntimeError):
                record_ton_provider_observation(
                    invoice_id="gri_monitor", provider_id="toncenter",
                    trace=self.trace(), engine=self.engine
                )


if __name__ == "__main__":
    unittest.main()
