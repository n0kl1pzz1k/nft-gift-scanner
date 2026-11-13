import tempfile
import time
import unittest
import uuid
from pathlib import Path

from sqlalchemy import create_engine, insert, text
from sqlalchemy.exc import DBAPIError

from financial_ledger import (
    get_account_balance, init_financial_schema, invoices, post_journal_entry,
    ton_chain_observations, ton_trace_evidence_hash, withdrawal_events,
    settle_verified_ton_payment, verify_ton_payment_trace,
    request_ton_withdrawal, advance_withdrawal_to_processing,
    mark_withdrawal_submitted, confirm_ton_withdrawal,
    release_ton_withdrawal, quarantine_ton_withdrawal,
)


class FinancialLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name)/'ledger.sqlite3'}", future=True)
        init_financial_schema(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def record_quorum(self, invoice, trace):
        digest = ton_trace_evidence_hash(trace)
        with self.engine.begin() as conn:
            for provider_id in ("toncenter", "tonapi"):
                conn.execute(insert(ton_chain_observations).values(
                    observation_id=str(uuid.uuid4()), network="ton-mainnet",
                    tx_hash=trace["tx_hash"], event_index=int(trace["event_index"]),
                    asset_id=invoice["asset_id"], provider_id=provider_id,
                    evidence_hash=digest, finalized=True, observed_at=int(time.time()),
                ))

    def postings(self, amount=100):
        return [
            {"account_id": "asset:clearing:TON", "account_type": "asset", "currency": "TON", "amount_raw": amount},
            {"account_id": "liability:user:1:TON", "account_type": "liability", "currency": "TON", "amount_raw": -amount},
        ]

    def test_balanced_entry_and_computed_balance(self):
        result = post_journal_entry(idempotency_key="test-1", source_type="test", source_reference="ref",
                                    postings=self.postings(), engine=self.engine)
        self.assertFalse(result["duplicate"])
        self.assertEqual(get_account_balance("asset:clearing:TON", "TON", self.engine), 100)

    def test_unbalanced_entry_rejected(self):
        postings = self.postings()
        postings[1]["amount_raw"] = -99
        with self.assertRaises(ValueError):
            post_journal_entry(idempotency_key="bad", source_type="test", source_reference="x",
                               postings=postings, engine=self.engine)

    def test_duplicate_idempotency_key_is_harmless(self):
        a = post_journal_entry(idempotency_key="same", source_type="test", source_reference="ref",
                               postings=self.postings(), engine=self.engine)
        b = post_journal_entry(idempotency_key="same", source_type="test", source_reference="ref",
                               postings=self.postings(), engine=self.engine)
        self.assertEqual(a["entry_id"], b["entry_id"])
        self.assertTrue(b["duplicate"])

    def test_idempotency_key_payload_collision_rejected(self):
        post_journal_entry(idempotency_key="same", source_type="test", source_reference="ref",
                           postings=self.postings(), engine=self.engine)
        with self.assertRaises(ValueError):
            post_journal_entry(idempotency_key="same", source_type="test", source_reference="ref",
                               postings=self.postings(200), engine=self.engine)

    def test_ledger_is_append_only(self):
        post_journal_entry(idempotency_key="append", source_type="test", source_reference="ref",
                           postings=self.postings(), engine=self.engine)
        with self.assertRaises(DBAPIError), self.engine.begin() as conn:
            conn.execute(text("UPDATE ledger_entries SET source_type='changed'"))

    def test_verified_ton_payment_posts_once_and_replay_is_harmless(self):
        invoice = {
            "invoice_id": "gri_test", "user_id": "42", "asset_id": "usdt-ton", "chain": "ton-mainnet",
            "master_address": "master", "recipient_address": "wallet", "amount_raw": "100",
            "payload": "invoice-payload", "expires_at": int(time.time()) + 600,
            "status": "pending", "created_at": int(time.time()),
        }
        with self.engine.begin() as conn:
            conn.execute(insert(invoices).values(**invoice))
        trace = {
            "chain": "ton-mainnet", "master_address": "master", "source_jetton_wallet": "jetton-wallet",
            "expected_source_jetton_wallet": "jetton-wallet", "recipient_address": "wallet",
            "amount_raw": 100, "payload": "invoice-payload", "opcode": "0x7362d09c",
            "successful": True, "bounced": False, "finalized": True,
            "tx_hash": "transaction-hash", "event_index": 0,
        }
        self.record_quorum(invoice, trace)
        first = settle_verified_ton_payment(invoice_id="gri_test", trace=trace, engine=self.engine)
        second = settle_verified_ton_payment(invoice_id="gri_test", trace=trace, engine=self.engine)
        self.assertTrue(first["credited"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(get_account_balance("liability:user:42:usdt-ton", "usdt-ton", self.engine), -100)

    def test_crypto_invoice_creation_is_disabled_by_default(self):
        from financial_ledger import create_payment_invoice
        with self.assertRaises(PermissionError):
            create_payment_invoice(user_id="1", asset_id="usdt-ton", chain="ton-mainnet",
                master_address="EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs",
                recipient_address="wallet", amount_raw=100, payload="disabled",
                expires_at=9999999999, engine=self.engine)

    def test_ton_trace_fails_closed(self):
        invoice = {"chain": "ton-mainnet", "master_address": "master", "recipient_address": "wallet",
                   "amount_raw": "100", "payload": "inv"}
        trace = {
            "chain": "ton-mainnet", "master_address": "master", "source_jetton_wallet": "jw",
            "expected_source_jetton_wallet": "jw", "recipient_address": "other", "amount_raw": 100,
            "payload": "inv", "opcode": "0x7362d09c", "successful": True, "bounced": False,
            "finalized": True, "tx_hash": "abc", "event_index": 0,
        }
        with self.assertRaises(ValueError):
            verify_ton_payment_trace(trace, invoice)
        trace["recipient_address"] = "wallet"
        trace["finalized"] = False
        with self.assertRaises(ValueError):
            verify_ton_payment_trace(trace, invoice)


    def test_native_gram_transfer_is_verified_and_credited_once(self):
        invoice = {
            "invoice_id": "gri_native", "user_id": "77", "asset_id": "GRAM",
            "chain": "ton-mainnet", "master_address": "", "asset_kind": "native",
            "recipient_address": "EQrecipient", "amount_raw": "1000000000",
            "payload": "native-invoice", "expires_at": int(time.time()) + 600,
            "status": "pending", "created_at": int(time.time()),
        }
        with self.engine.begin() as conn:
            conn.execute(insert(invoices).values(**invoice))
        trace = {
            "chain": "ton-mainnet", "asset_kind": "native",
            "sender_address": "EQsender", "recipient_address": "EQrecipient",
            "amount_raw": 1000000000, "payload": "native-invoice",
            "successful": True, "bounced": False, "finalized": True,
            "tx_hash": "native-transaction", "event_index": 0,
        }
        self.record_quorum(invoice, trace)
        first = settle_verified_ton_payment(invoice_id="gri_native", trace=trace, engine=self.engine)
        second = settle_verified_ton_payment(invoice_id="gri_native", trace=trace, engine=self.engine)
        self.assertTrue(first["credited"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(get_account_balance("liability:user:77:GRAM", "GRAM", self.engine), -1000000000)

    def test_native_gram_cannot_be_faked_by_jetton_trace(self):
        invoice = {
            "chain": "ton-mainnet", "asset_id": "GRAM", "asset_kind": "native",
            "master_address": "", "recipient_address": "EQrecipient",
            "amount_raw": "1", "payload": "x",
        }
        trace = {
            "chain": "ton-mainnet", "asset_kind": "native", "sender_address": "EQsender",
            "master_address": "EQFakeJetton", "recipient_address": "EQrecipient",
            "amount_raw": 1, "payload": "x", "successful": True, "bounced": False,
            "finalized": True, "tx_hash": "fake", "event_index": 0,
        }
        with self.assertRaises(ValueError):
            verify_ton_payment_trace(trace, invoice)

    def test_withdrawal_reserves_balance_and_duplicate_request_cannot_double_spend(self):
        post_journal_entry(idempotency_key="seed:withdrawal", source_type="test_deposit",
            source_reference="seed", postings=[
                {"account_id": "asset:clearing:GRAM", "account_type": "asset",
                 "currency": "GRAM", "amount_raw": 100},
                {"account_id": "liability:user:42:GRAM", "account_type": "liability",
                 "currency": "GRAM", "amount_raw": -100},
            ], engine=self.engine)
        first = request_ton_withdrawal(user_id="42", asset_id="GRAM",
            destination_address="EQdestination", amount_raw=70, fee_raw=10,
            idempotency_key="withdrawal-1", engine=self.engine)
        second = request_ton_withdrawal(user_id="42", asset_id="GRAM",
            destination_address="EQdestination", amount_raw=70, fee_raw=10,
            idempotency_key="withdrawal-1", engine=self.engine)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text(
                "SELECT COUNT(*) FROM withdrawal_events WHERE request_id=:id"
            ), {"id": first["request_id"]}).scalar_one(), 1)
        with self.assertRaises(DBAPIError), self.engine.begin() as conn:
            conn.execute(text(
                "UPDATE withdrawal_events SET event_type='failed' WHERE request_id=:id"
            ), {"id": first["request_id"]})
        self.assertEqual(get_account_balance("liability:user:42:GRAM", "GRAM", self.engine), -20)
        with self.assertRaises(ValueError):
            request_ton_withdrawal(user_id="42", asset_id="GRAM",
                destination_address="EQother", amount_raw=30, idempotency_key="withdrawal-2",
                engine=self.engine)
        advance_withdrawal_to_processing(request_id=first["request_id"], engine=self.engine)
        mark_withdrawal_submitted(request_id=first["request_id"], tx_hash="outgoing-tx", engine=self.engine)
        settled = confirm_ton_withdrawal(request_id=first["request_id"], tx_hash="outgoing-tx", engine=self.engine)
        duplicate = confirm_ton_withdrawal(request_id=first["request_id"], tx_hash="outgoing-tx", engine=self.engine)
        self.assertTrue(settled["confirmed"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(get_account_balance("liability:withdrawal_hold:"+first["request_id"]+":GRAM", "GRAM", self.engine), 0)
        self.assertEqual(get_account_balance("asset:clearing:GRAM", "GRAM", self.engine), 20)

    def test_ambiguous_withdrawal_cannot_release_funds(self):
        post_journal_entry(idempotency_key="seed:withdrawal2", source_type="test_deposit",
            source_reference="seed2", postings=[
                {"account_id": "asset:clearing:usdt-ton", "account_type": "asset",
                 "currency": "usdt-ton", "amount_raw": 50},
                {"account_id": "liability:user:43:usdt-ton", "account_type": "liability",
                 "currency": "usdt-ton", "amount_raw": -50},
            ], engine=self.engine)
        req = request_ton_withdrawal(user_id="43", asset_id="usdt-ton",
            destination_address="EQdestination", amount_raw=30,
            idempotency_key="withdrawal-ambiguous", engine=self.engine)
        advance_withdrawal_to_processing(request_id=req["request_id"], engine=self.engine)
        quarantine_ton_withdrawal(request_id=req["request_id"],
            reason="broadcast result timed out", engine=self.engine)
        with self.assertRaises(PermissionError):
            release_ton_withdrawal(request_id=req["request_id"], reason="timeout",
                definitive_not_broadcast=False, engine=self.engine)
        self.assertEqual(get_account_balance("liability:user:43:usdt-ton", "usdt-ton", self.engine), -20)


if __name__ == "__main__":
    unittest.main()
