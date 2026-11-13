import tempfile
import time
import unittest
from pathlib import Path

from sqlalchemy import create_engine, insert, text
from sqlalchemy.exc import DBAPIError

from financial_ledger import (
    get_account_balance, init_financial_schema, invoices, post_journal_entry,
    settle_verified_ton_payment, verify_ton_payment_trace,
)

class FinancialLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.engine=create_engine(f"sqlite:///{Path(self.temp.name)/'ledger.sqlite3'}",future=True)
        init_financial_schema(self.engine)
    def tearDown(self):
        self.engine.dispose(); self.temp.cleanup()
    def postings(self, amount=100):
        return [
          {"account_id":"asset:clearing:TON","account_type":"asset","currency":"TON","amount_raw":amount},
          {"account_id":"liability:user:1:TON","account_type":"liability","currency":"TON","amount_raw":-amount}]
    def test_balanced_entry_and_computed_balance(self):
        result=post_journal_entry(idempotency_key="test-1",source_type="test",source_reference="ref",
            postings=self.postings(),engine=self.engine)
        self.assertFalse(result["duplicate"])
        self.assertEqual(get_account_balance("asset:clearing:TON","TON",self.engine),100)
    def test_unbalanced_entry_rejected(self):
        with self.assertRaises(ValueError):
            post_journal_entry(idempotency_key="bad",source_type="test",source_reference="x",
                postings=self.postings(101),engine=self.engine)
    def test_duplicate_idempotency_key_is_harmless(self):
        a=post_journal_entry(idempotency_key="same",source_type="test",source_reference="ref",postings=self.postings(),engine=self.engine)
        b=post_journal_entry(idempotency_key="same",source_type="test",source_reference="ref",postings=self.postings(),engine=self.engine)
        self.assertEqual(a["entry_id"],b["entry_id"]); self.assertTrue(b["duplicate"])
    def test_idempotency_key_payload_collision_rejected(self):
        post_journal_entry(idempotency_key="same",source_type="test",source_reference="ref",postings=self.postings(),engine=self.engine)
        with self.assertRaises(ValueError):
            post_journal_entry(idempotency_key="same",source_type="test",source_reference="ref",postings=self.postings(200),engine=self.engine)
    def test_ledger_is_append_only(self):
        post_journal_entry(idempotency_key="append",source_type="test",source_reference="ref",postings=self.postings(),engine=self.engine)
        with self.assertRaises(DBAPIError), self.engine.begin() as conn:
            conn.execute(text("UPDATE ledger_entries SET source_type='changed'"))

    def test_verified_ton_payment_posts_once_and_replay_is_harmless(self):
        invoice = {"invoice_id":"gri_test","user_id":"42","asset_id":"usdt-ton","chain":"ton-mainnet",
            "master_address":"master","recipient_address":"wallet","amount_raw":"100",
            "payload":"invoice-payload","expires_at":int(time.time())+600,"status":"pending","created_at":int(time.time())}
        with self.engine.begin() as conn:
            conn.execute(insert(invoices).values(**invoice))
        trace = {"chain":"ton-mainnet","master_address":"master","source_jetton_wallet":"jetton-wallet",
            "expected_source_jetton_wallet":"jetton-wallet","recipient_address":"wallet","amount_raw":100,
            "payload":"invoice-payload","opcode":"0x7362d09c","successful":True,"bounced":False,
            "finalized":True,"tx_hash":"transaction-hash","event_index":0}
        first = settle_verified_ton_payment(invoice_id="gri_test",trace=trace,engine=self.engine)
        second = settle_verified_ton_payment(invoice_id="gri_test",trace=trace,engine=self.engine)
        self.assertTrue(first["credited"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(get_account_balance("liability:user:42:usdt-ton","usdt-ton",self.engine),-100)

    def test_crypto_invoice_creation_is_disabled_by_default(self):
        from financial_ledger import create_payment_invoice
        with self.assertRaises(PermissionError):
            create_payment_invoice(user_id="1",asset_id="usdt-ton",chain="ton-mainnet",
                master_address="EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs",
                recipient_address="wallet",amount_raw=100,payload="disabled",expires_at=9999999999,
                engine=self.engine)

    def test_ton_trace_fails_closed(self):
        invoice={"chain":"ton-mainnet","master_address":"master","recipient_address":"wallet","amount_raw":"100","payload":"inv"}
        trace={"chain":"ton-mainnet","master_address":"master","source_jetton_wallet":"jw","expected_source_jetton_wallet":"jw",
          "recipient_address":"other","amount_raw":100,"payload":"inv","opcode":"0x7362d09c","successful":True,
          "bounced":False,"finalized":True,"tx_hash":"abc","event_index":0}
        with self.assertRaises(ValueError): verify_ton_payment_trace(trace,invoice)
        trace["recipient_address"]="wallet"; trace["finalized"]=False
        with self.assertRaises(ValueError): verify_ton_payment_trace(trace,invoice)

if __name__=="__main__": unittest.main()
