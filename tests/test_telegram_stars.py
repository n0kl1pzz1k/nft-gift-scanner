import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from financial_ledger import (
    approve_telegram_stars_pre_checkout,
    create_telegram_stars_invoice,
    get_account_balance,
    init_financial_schema,
    settle_telegram_stars_payment,
    record_telegram_stars_terms_acceptance,
    request_telegram_stars_refund,
    prepare_telegram_stars_refund,
    complete_telegram_stars_refund,
    fail_telegram_stars_refund,
    require_telegram_stars_refund_reconciliation,
)


class TelegramStarsLedgerTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {'MARKETPLACE_REAL_DEALS_ENABLED': 'true', 'TELEGRAM_STARS_TERMS_VERSION': 'terms-v1', 'TERMS_URL': 'https://example.invalid/terms'})
        self.env.start()
        self.engine = create_engine(
            "sqlite://",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        with self.engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE demo_orders (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE demo_deals (
                    id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    listing_owner_id TEXT NOT NULL,
                    offer_sender_id TEXT NOT NULL,
                    agreed_amount TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    is_real INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL
                )
            """))
            connection.execute(text("""
                INSERT INTO demo_orders(id,kind,status,title)
                VALUES ('order-1','listing','matched','Golden Gift')
            """))
            connection.execute(text("""
                INSERT INTO demo_deals(id,order_id,listing_owner_id,offer_sender_id,agreed_amount,currency,is_real,status)
                VALUES ('deal-1','order-1','10010','10020','75','XTR',1,'payment_not_enabled')
            """))
        init_financial_schema(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.env.stop()

    def _invoice(self):
        record_telegram_stars_terms_acceptance(
            user_id="10020", engine=self.engine, now=900
        )
        return create_telegram_stars_invoice(
            deal_id="deal-1", buyer_user_id="10020", engine=self.engine, now=1000
        )

    def test_current_terms_acceptance_is_required_and_idempotent(self):
        with self.assertRaises(PermissionError):
            create_telegram_stars_invoice(
                deal_id="deal-1", buyer_user_id="10020", engine=self.engine, now=1000
            )
        first = record_telegram_stars_terms_acceptance(
            user_id="10020", engine=self.engine, now=900
        )
        second = record_telegram_stars_terms_acceptance(
            user_id="10020", engine=self.engine, now=901
        )
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["acceptance_id"], second["acceptance_id"])
        self.assertEqual(self._invoice()["amount_stars"], 75)

    def test_terms_acceptance_audit_record_is_append_only(self):
        acceptance = record_telegram_stars_terms_acceptance(
            user_id="10020", engine=self.engine, now=900
        )
        with self.assertRaises(Exception):
            with self.engine.begin() as connection:
                connection.execute(text(
                    "UPDATE telegram_stars_terms_acceptances SET terms_url='https://tampered.invalid' "
                    "WHERE acceptance_id=:id"
                ), {"id": acceptance["acceptance_id"]})

    def test_terms_acceptance_is_bound_to_current_version_and_https_url(self):
        with patch.dict("os.environ", {"TELEGRAM_STARS_TERMS_VERSION": "terms-v2"}):
            with self.assertRaises(ValueError):
                record_telegram_stars_terms_acceptance(
                    user_id="10020", terms_version="terms-v1", engine=self.engine, now=900
                )
        with patch.dict("os.environ", {"TERMS_URL": "http://example.invalid/terms"}):
            with self.assertRaises(RuntimeError):
                record_telegram_stars_terms_acceptance(
                    user_id="10020", engine=self.engine, now=900
                )

    def test_real_deal_feature_flag_is_fail_closed(self):
        with patch.dict("os.environ", {"MARKETPLACE_REAL_DEALS_ENABLED": "false"}):
            with self.assertRaises(PermissionError):
                self._invoice()

    def test_demo_deal_cannot_be_charged(self):
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE demo_deals SET is_real=0 WHERE id='deal-1'"))
        with self.assertRaises(PermissionError):
            self._invoice()

    def test_precheckout_rejects_deal_that_became_demo(self):
        invoice = self._invoice()
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE demo_deals SET is_real=0 WHERE id='deal-1'"))
        self.assertFalse(approve_telegram_stars_pre_checkout(
            payload=invoice["payload"], buyer_user_id="10020", currency="XTR",
            amount_stars=75, query_id="query-demo", engine=self.engine, now=1010
        ))

    def test_only_buyer_can_create_stars_invoice(self):
        record_telegram_stars_terms_acceptance(user_id="10010", engine=self.engine, now=900)
        with self.assertRaises(PermissionError):
            create_telegram_stars_invoice(
                deal_id="deal-1", buyer_user_id="10010", engine=self.engine, now=1000
            )

    def test_only_xtr_and_integer_amounts_are_accepted(self):
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE demo_deals SET currency='TON' WHERE id='deal-1'"))
        with self.assertRaises(PermissionError):
            self._invoice()
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE demo_deals SET currency='XTR', agreed_amount='75.5' WHERE id='deal-1'"))
        with self.assertRaises(ValueError):
            self._invoice()

    def test_invoice_creation_is_idempotent(self):
        first = self._invoice()
        second = self._invoice()
        self.assertEqual(first["invoice_id"], second["invoice_id"])
        self.assertEqual(first["payload"], second["payload"])
        self.assertEqual(first["amount_stars"], 75)

    def test_precheckout_validates_identity_amount_currency_and_single_use(self):
        invoice = self._invoice()
        self.assertFalse(approve_telegram_stars_pre_checkout(
            payload=invoice["payload"], buyer_user_id="10020", currency="XTR",
            amount_stars=74, query_id="query-1", engine=self.engine, now=1010
        ))
        self.assertFalse(approve_telegram_stars_pre_checkout(
            payload=invoice["payload"], buyer_user_id="10010", currency="XTR",
            amount_stars=75, query_id="query-1", engine=self.engine, now=1010
        ))
        self.assertTrue(approve_telegram_stars_pre_checkout(
            payload=invoice["payload"], buyer_user_id="10020", currency="XTR",
            amount_stars=75, query_id="query-1", engine=self.engine, now=1010
        ))
        self.assertTrue(approve_telegram_stars_pre_checkout(
            payload=invoice["payload"], buyer_user_id="10020", currency="XTR",
            amount_stars=75, query_id="query-1", engine=self.engine, now=1011
        ))
        self.assertFalse(approve_telegram_stars_pre_checkout(
            payload=invoice["payload"], buyer_user_id="10020", currency="XTR",
            amount_stars=75, query_id="query-2", engine=self.engine, now=1011
        ))

    def test_successful_payment_is_atomic_balanced_and_idempotent(self):
        invoice = self._invoice()
        approved = approve_telegram_stars_pre_checkout(
            payload=invoice["payload"], buyer_user_id="10020", currency="XTR",
            amount_stars=75, query_id="query-1", engine=self.engine, now=1010
        )
        self.assertTrue(approved)
        result = settle_telegram_stars_payment(
            payload=invoice["payload"], buyer_user_id="10020", currency="XTR",
            amount_stars=75, telegram_payment_charge_id="telegram-charge-1",
            provider_payment_charge_id=None, engine=self.engine, now=1020
        )
        self.assertTrue(result["credited"])
        self.assertEqual(get_account_balance("asset:telegram_stars:clearing", "XTR", self.engine), 75)
        self.assertEqual(get_account_balance("liability:seller:10010:XTR", "XTR", self.engine), -75)
        with self.engine.connect() as connection:
            status = connection.execute(text("SELECT status FROM demo_deals WHERE id='deal-1'")).scalar_one()
        self.assertEqual(status, "paid")

        duplicate = settle_telegram_stars_payment(
            payload=invoice["payload"], buyer_user_id="10020", currency="XTR",
            amount_stars=75, telegram_payment_charge_id="telegram-charge-1",
            provider_payment_charge_id=None, engine=self.engine, now=1030
        )
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(get_account_balance("asset:telegram_stars:clearing", "XTR", self.engine), 75)

    def test_payment_cannot_settle_without_precheckout_approval(self):
        invoice = self._invoice()
        with self.assertRaises(ValueError):
            settle_telegram_stars_payment(
                payload=invoice["payload"], buyer_user_id="10020", currency="XTR",
                amount_stars=75, telegram_payment_charge_id="charge-no-precheckout",
                engine=self.engine, now=1010
            )

    def test_payment_events_are_append_only(self):
        invoice = self._invoice()
        approve_telegram_stars_pre_checkout(
            payload=invoice["payload"], buyer_user_id="10020", currency="XTR",
            amount_stars=75, query_id="query-1", engine=self.engine, now=1010
        )
        settle_telegram_stars_payment(
            payload=invoice["payload"], buyer_user_id="10020", currency="XTR",
            amount_stars=75, telegram_payment_charge_id="telegram-charge-1",
            engine=self.engine, now=1020
        )
        with self.assertRaises(Exception):
            with self.engine.begin() as connection:
                connection.execute(text(
                    "UPDATE telegram_stars_payments SET amount_stars=1 WHERE telegram_payment_charge_id='telegram-charge-1'"
                ))


    def _settle_paid_invoice(self):
        invoice = self._invoice()
        approved = approve_telegram_stars_pre_checkout(
            payload=invoice["payload"], buyer_user_id="10020", currency="XTR",
            amount_stars=75, query_id="refund-query", engine=self.engine, now=1010
        )
        self.assertTrue(approved)
        settled = settle_telegram_stars_payment(
            payload=invoice["payload"], buyer_user_id="10020", currency="XTR",
            amount_stars=75, telegram_payment_charge_id="refund-charge",
            engine=self.engine, now=1020
        )
        self.assertTrue(settled["credited"])
        return invoice

    def test_buyer_can_request_refund_and_admin_confirmation_reverses_ledger(self):
        invoice = self._settle_paid_invoice()
        requested = request_telegram_stars_refund(
            invoice_id=invoice["invoice_id"], buyer_user_id="10020",
            reason="Gift delivery failed", engine=self.engine, now=1030
        )
        self.assertEqual(requested["status"], "requested")
        duplicate = request_telegram_stars_refund(
            invoice_id=invoice["invoice_id"], buyer_user_id="10020",
            reason="Duplicate request", engine=self.engine, now=1031
        )
        self.assertTrue(duplicate["duplicate"])
        claimed = prepare_telegram_stars_refund(
            request_id=requested["request_id"], admin_user_id="90001",
            engine=self.engine, now=1040
        )
        self.assertEqual(claimed["telegram_payment_charge_id"], "refund-charge")
        result = complete_telegram_stars_refund(
            request_id=requested["request_id"], telegram_response_ref="refund-charge",
            engine=self.engine, now=1050
        )
        self.assertTrue(result["refunded"])
        duplicate_result = complete_telegram_stars_refund(
            request_id=requested["request_id"], engine=self.engine, now=1051
        )
        self.assertTrue(duplicate_result["duplicate"])
        self.assertEqual(get_account_balance("asset:telegram_stars:clearing", "XTR", self.engine), 0)
        self.assertEqual(get_account_balance("liability:seller:10010:XTR", "XTR", self.engine), 0)
        with self.engine.connect() as connection:
            status = connection.execute(text(
                "SELECT status FROM telegram_stars_invoices WHERE invoice_id=:id"
            ), {"id": invoice["invoice_id"]}).scalar_one()
        self.assertEqual(status, "refunded")

    def test_only_original_buyer_can_request_refund(self):
        invoice = self._settle_paid_invoice()
        with self.assertRaises(PermissionError):
            request_telegram_stars_refund(
                invoice_id=invoice["invoice_id"], buyer_user_id="10010",
                reason="not the buyer", engine=self.engine, now=1030
            )

    def test_definite_refund_failure_can_be_retried_but_processing_cannot(self):
        invoice = self._settle_paid_invoice()
        requested = request_telegram_stars_refund(
            invoice_id=invoice["invoice_id"], buyer_user_id="10020",
            reason="Refund requested", engine=self.engine, now=1030
        )
        prepare_telegram_stars_refund(
            request_id=requested["request_id"], admin_user_id="90001",
            engine=self.engine, now=1040
        )
        with self.assertRaises(ValueError):
            prepare_telegram_stars_refund(
                request_id=requested["request_id"], admin_user_id="90002",
                engine=self.engine, now=1041
            )
        fail_telegram_stars_refund(
            request_id=requested["request_id"], reason="Definite Telegram rejection",
            engine=self.engine, now=1042
        )
        retried = request_telegram_stars_refund(
            invoice_id=invoice["invoice_id"], buyer_user_id="10020",
            reason="Retry after confirmed rejection", engine=self.engine, now=1043
        )
        self.assertEqual(retried["request_id"], requested["request_id"])
        prepare_telegram_stars_refund(
            request_id=retried["request_id"], admin_user_id="90001",
            engine=self.engine, now=1044
        )
        self.assertEqual(
            get_account_balance("asset:telegram_stars:clearing", "XTR", self.engine), 75
        )


    def test_ambiguous_refund_is_quarantined_and_cannot_be_retried(self):
        invoice = self._settle_paid_invoice()
        requested = request_telegram_stars_refund(
            invoice_id=invoice["invoice_id"], buyer_user_id="10020",
            reason="Refund requested", engine=self.engine, now=1030
        )
        prepare_telegram_stars_refund(
            request_id=requested["request_id"], admin_user_id="90001",
            engine=self.engine, now=1040
        )
        require_telegram_stars_refund_reconciliation(
            request_id=requested["request_id"],
            reason="Telegram response timed out after request", engine=self.engine, now=1041
        )
        with self.engine.connect() as connection:
            row = connection.execute(text(
                "SELECT status, reconciliation_note FROM telegram_stars_refund_requests WHERE request_id=:id"
            ), {"id": requested["request_id"]}).mappings().one()
        self.assertEqual(row["status"], "reconciliation_required")
        self.assertIn("timed out", row["reconciliation_note"])
        with self.assertRaises(ValueError):
            prepare_telegram_stars_refund(
                request_id=requested["request_id"], admin_user_id="90002",
                engine=self.engine, now=1042
            )
        duplicate = request_telegram_stars_refund(
            invoice_id=invoice["invoice_id"], buyer_user_id="10020",
            reason="Retry attempt", engine=self.engine, now=1043
        )
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["status"], "reconciliation_required")



if __name__ == "__main__":
    unittest.main()
