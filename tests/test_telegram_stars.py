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
)


class TelegramStarsLedgerTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {'MARKETPLACE_REAL_DEALS_ENABLED': 'true'})
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
                VALUES ('deal-1','order-1','seller-10','buyer-20','75','XTR',1,'payment_not_enabled')
            """))
        init_financial_schema(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.env.stop()

    def _invoice(self):
        return create_telegram_stars_invoice(
            deal_id="deal-1", buyer_user_id="buyer-20", engine=self.engine, now=1000
        )

    def test_only_buyer_can_create_stars_invoice(self):
        with self.assertRaises(PermissionError):
            create_telegram_stars_invoice(
                deal_id="deal-1", buyer_user_id="seller-10", engine=self.engine, now=1000
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
            payload=invoice["payload"], buyer_user_id="buyer-20", currency="XTR",
            amount_stars=74, query_id="query-1", engine=self.engine, now=1010
        ))
        self.assertFalse(approve_telegram_stars_pre_checkout(
            payload=invoice["payload"], buyer_user_id="seller-10", currency="XTR",
            amount_stars=75, query_id="query-1", engine=self.engine, now=1010
        ))
        self.assertTrue(approve_telegram_stars_pre_checkout(
            payload=invoice["payload"], buyer_user_id="buyer-20", currency="XTR",
            amount_stars=75, query_id="query-1", engine=self.engine, now=1010
        ))
        self.assertTrue(approve_telegram_stars_pre_checkout(
            payload=invoice["payload"], buyer_user_id="buyer-20", currency="XTR",
            amount_stars=75, query_id="query-1", engine=self.engine, now=1011
        ))
        self.assertFalse(approve_telegram_stars_pre_checkout(
            payload=invoice["payload"], buyer_user_id="buyer-20", currency="XTR",
            amount_stars=75, query_id="query-2", engine=self.engine, now=1011
        ))

    def test_successful_payment_is_atomic_balanced_and_idempotent(self):
        invoice = self._invoice()
        approved = approve_telegram_stars_pre_checkout(
            payload=invoice["payload"], buyer_user_id="buyer-20", currency="XTR",
            amount_stars=75, query_id="query-1", engine=self.engine, now=1010
        )
        self.assertTrue(approved)
        result = settle_telegram_stars_payment(
            payload=invoice["payload"], buyer_user_id="buyer-20", currency="XTR",
            amount_stars=75, telegram_payment_charge_id="telegram-charge-1",
            provider_payment_charge_id=None, engine=self.engine, now=1020
        )
        self.assertTrue(result["credited"])
        self.assertEqual(get_account_balance("asset:telegram_stars:clearing", "XTR", self.engine), 75)
        self.assertEqual(get_account_balance("liability:seller:seller-10:XTR", "XTR", self.engine), -75)
        with self.engine.connect() as connection:
            status = connection.execute(text("SELECT status FROM demo_deals WHERE id='deal-1'")).scalar_one()
        self.assertEqual(status, "paid")

        duplicate = settle_telegram_stars_payment(
            payload=invoice["payload"], buyer_user_id="buyer-20", currency="XTR",
            amount_stars=75, telegram_payment_charge_id="telegram-charge-1",
            provider_payment_charge_id=None, engine=self.engine, now=1030
        )
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(get_account_balance("asset:telegram_stars:clearing", "XTR", self.engine), 75)

    def test_payment_cannot_settle_without_precheckout_approval(self):
        invoice = self._invoice()
        with self.assertRaises(ValueError):
            settle_telegram_stars_payment(
                payload=invoice["payload"], buyer_user_id="buyer-20", currency="XTR",
                amount_stars=75, telegram_payment_charge_id="charge-no-precheckout",
                engine=self.engine, now=1010
            )

    def test_payment_events_are_append_only(self):
        invoice = self._invoice()
        approve_telegram_stars_pre_checkout(
            payload=invoice["payload"], buyer_user_id="buyer-20", currency="XTR",
            amount_stars=75, query_id="query-1", engine=self.engine, now=1010
        )
        settle_telegram_stars_payment(
            payload=invoice["payload"], buyer_user_id="buyer-20", currency="XTR",
            amount_stars=75, telegram_payment_charge_id="telegram-charge-1",
            engine=self.engine, now=1020
        )
        with self.assertRaises(Exception):
            with self.engine.begin() as connection:
                connection.execute(text(
                    "UPDATE telegram_stars_payments SET amount_stars=1 WHERE telegram_payment_charge_id='telegram-charge-1'"
                ))


if __name__ == "__main__":
    unittest.main()
