import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import marketplace_offers
import marketplace


class LegacyDealMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "legacy.sqlite3"
        self.marketplace_db_patch = patch.object(marketplace, "DB_PATH", str(self.database))
        self.offers_db_patch = patch.object(demo_offers, "DB_PATH", str(self.database))
        self.marketplace_db_patch.start()
        self.offers_db_patch.start()

        marketplace.init_marketplace_db()
        with marketplace._db() as conn:
            conn.execute(
                """
                INSERT INTO demo_orders
                    (id, user_id, owner_name, kind, title, price, currency,
                     status, created_at, updated_at, note, asset_type, asset_id)
                VALUES ('order-1', 'seller-1', 'Seller', 'listing', 'Gift',
                        '10', 'TON', 'active', 1, 1, '', 'gift', 'asset-1')
                """
            )
            conn.execute(
                """
                CREATE TABLE demo_offers (
                    id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    sender_user_id TEXT NOT NULL,
                    sender_name TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    currency TEXT NOT NULL CHECK(currency IN ('TON', 'XTR')),
                    note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('pending', 'accepted', 'declined', 'withdrawn')),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES demo_orders(id)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO demo_offers
                    (id, order_id, sender_user_id, sender_name, amount,
                     currency, note, status, created_at, updated_at)
                VALUES ('offer-1', 'order-1', 'buyer-1', 'Buyer', '10',
                        'TON', '', 'accepted', 2, 2)
                """
            )
            # This is the legacy shape: one permanent deal per order and no status column.
            conn.execute(
                """
                CREATE TABLE demo_deals (
                    id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL UNIQUE,
                    offer_id TEXT NOT NULL UNIQUE,
                    listing_owner_id TEXT NOT NULL,
                    offer_sender_id TEXT NOT NULL,
                    agreed_amount TEXT NOT NULL,
                    currency TEXT NOT NULL CHECK(currency IN ('TON', 'XTR')),
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES demo_orders(id),
                    FOREIGN KEY(offer_id) REFERENCES demo_offers(id)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO demo_deals
                    (id, order_id, offer_id, listing_owner_id,
                     offer_sender_id, agreed_amount, currency, created_at)
                VALUES ('deal-1', 'order-1', 'offer-1', 'seller-1',
                        'buyer-1', '10', 'TON', 3)
                """
            )

    def tearDown(self):
        self.offers_db_patch.stop()
        self.marketplace_db_patch.stop()
        self.temp_dir.cleanup()

    def test_rebuild_preserves_legacy_rows_and_adds_default_status(self):
        marketplace_offers.init_marketplace_offers_db()

        with marketplace._db() as conn:
            deal = conn.execute(
                "SELECT * FROM demo_deals WHERE id = 'deal-1'"
            ).fetchone()
            self.assertIsNotNone(deal)
            self.assertEqual(deal["offer_id"], "offer-1")
            self.assertEqual(deal["status"], "payment_not_enabled")

            conn.execute(
                "UPDATE demo_deals SET status = 'cancelled' WHERE id = 'deal-1'"
            )
            conn.execute(
                """
                INSERT INTO demo_offers
                    (id, order_id, sender_user_id, sender_name, amount,
                     currency, note, status, created_at, updated_at)
                VALUES ('offer-2', 'order-1', 'buyer-2', 'Buyer 2', '11',
                        'TON', '', 'accepted', 4, 4)
                """
            )
            conn.execute(
                """
                INSERT INTO demo_deals
                    (id, order_id, offer_id, listing_owner_id,
                     offer_sender_id, agreed_amount, currency, created_at)
                VALUES ('deal-2', 'order-1', 'offer-2', 'seller-1',
                        'buyer-2', '11', 'TON', 5)
                """
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM demo_deals WHERE order_id = 'order-1'"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

        # Re-running initialization must remain safe and preserve both deals.
        marketplace_offers.init_marketplace_offers_db()
        with marketplace._db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM demo_deals").fetchone()[0],
                2,
            )


if __name__ == "__main__":
    unittest.main()
