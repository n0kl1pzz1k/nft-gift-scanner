import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from marketplace_inventory import (
    init_inventory_schema, record_wallet_owned_nft, create_escrow_pending_listing,
    confirm_nft_escrow_deposit, reserve_verified_listing, cancel_listing_reservation, cancel_seller_listing,
    save_verified_wallet, revoke_verified_wallet, get_active_verified_wallet,
)


class SellerInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name)/'inventory.sqlite3'}", future=True)
        init_inventory_schema(self.engine)
        self.evidence = {
            "verified": True, "owner_wallet_address": "0:" + "11" * 32,
            "collection_address": "0:" + "22" * 32, "token_id": "7",
            "item_address": "0:" + "33" * 32, "evidence_hash": "a" * 64,
            "provider_ids": ["tonapi", "toncenter"],
        }
        self.asset = record_wallet_owned_nft(
            user_id="101", wallet_address=self.evidence["owner_wallet_address"],
            asset_type="collectible_username",
            collection_address=self.evidence["collection_address"], token_id="7",
            item_address=self.evidence["item_address"], evidence=self.evidence,
            engine=self.engine, now=100,
        )

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def listing(self):
        return create_escrow_pending_listing(
            user_id="101", asset_id=self.asset["asset_id"], price_raw=250,
            currency="TON", idempotency_key="seller-listing-key-0001",
            engine=self.engine, now=110,
        )

    def mark_deposit_pending(self, listing):
        # Test fixture: model the state after the seller has submitted the NFT transfer.
        with self.engine.begin() as conn:
            result = conn.execute(text(
                "UPDATE marketplace_listings SET status='pending_verification' "
                "WHERE listing_id=:id AND status='draft'"
            ), {"id": listing["listing_id"]})
            self.assertEqual(result.rowcount, 1)

    def test_verified_wallet_binding_can_be_revoked(self):
        address = "0:" + "55" * 32
        save_verified_wallet(user_id="101", address=address,
                             network="ton-mainnet", engine=self.engine, now=150)
        self.assertEqual(get_active_verified_wallet(user_id="101", engine=self.engine), address)
        self.assertEqual(revoke_verified_wallet(user_id="101", engine=self.engine, now=160), 1)
        with self.assertRaises(PermissionError):
            get_active_verified_wallet(user_id="101", engine=self.engine)
        self.assertEqual(revoke_verified_wallet(user_id="101", engine=self.engine, now=170), 0)

    def test_listing_is_private_until_escrow_owner_is_confirmed(self):
        listing = self.listing()
        self.assertEqual(listing["status"], "draft")
        self.assertFalse(listing.get("public", True))
        with self.assertRaises(ValueError):
            reserve_verified_listing(
                listing_id=listing["listing_id"], buyer_user_id="202",
                engine=self.engine, now=120,
            )
        self.mark_deposit_pending(listing)
        active = confirm_nft_escrow_deposit(
            listing_id=listing["listing_id"], tx_hash="ab" * 32,
            event_index=0, verified_escrow_owner="EQescrow",
            expected_escrow_owner="EQescrow", evidence_hash="b" * 64,
            provider_ids=["tonapi", "toncenter"], engine=self.engine, now=130,
        )
        self.assertEqual(active["status"], "active")
        with self.engine.connect() as conn:
            state = conn.execute(text(
                "SELECT custody_state FROM marketplace_assets WHERE asset_id=:id"
            ), {"id": self.asset["asset_id"]}).scalar_one()
        self.assertEqual(state, "escrowed")

    def test_escrow_verification_requires_two_sources_and_expected_owner(self):
        listing = self.listing()
        with self.assertRaises(PermissionError):
            confirm_nft_escrow_deposit(
                listing_id=listing["listing_id"], tx_hash="x", event_index=0,
                verified_escrow_owner="EQattacker", expected_escrow_owner="EQescrow",
                evidence_hash="b" * 64, provider_ids=["tonapi", "toncenter"],
                engine=self.engine,
            )
        with self.assertRaises(PermissionError):
            confirm_nft_escrow_deposit(
                listing_id=listing["listing_id"], tx_hash="x", event_index=0,
                verified_escrow_owner="EQescrow", expected_escrow_owner="EQescrow",
                evidence_hash="b" * 64, provider_ids=["tonapi"],
                engine=self.engine,
            )

    def test_idempotency_and_seller_binding(self):
        first = self.listing()
        duplicate = self.listing()
        self.assertEqual(first["listing_id"], duplicate["listing_id"])
        self.assertTrue(duplicate["duplicate"])
        with self.assertRaises(PermissionError):
            create_escrow_pending_listing(
                user_id="202", asset_id=self.asset["asset_id"], price_raw=250,
                currency="TON", idempotency_key="other-listing-key-0001",
                engine=self.engine,
            )

    def test_reservation_is_atomic_and_buyer_can_cancel(self):
        listing = self.listing()
        self.mark_deposit_pending(listing)
        confirm_nft_escrow_deposit(
            listing_id=listing["listing_id"], tx_hash="ab" * 32,
            event_index=0, verified_escrow_owner="EQescrow",
            expected_escrow_owner="EQescrow", evidence_hash="b" * 64,
            provider_ids=["tonapi", "toncenter"], engine=self.engine, now=120,
        )
        reservation = reserve_verified_listing(
            listing_id=listing["listing_id"], buyer_user_id="202",
            engine=self.engine, now=130,
        )
        self.assertEqual(reservation["status"], "active")
        with self.assertRaises(ValueError):
            reserve_verified_listing(
                listing_id=listing["listing_id"], buyer_user_id="303",
                engine=self.engine, now=131,
            )
        with self.assertRaises(PermissionError):
            cancel_listing_reservation(
                reservation_id=reservation["reservation_id"], actor_user_id="303",
                engine=self.engine,
            )
        cancelled = cancel_listing_reservation(
            reservation_id=reservation["reservation_id"], actor_user_id="202",
            engine=self.engine, now=140,
        )
        self.assertTrue(cancelled["cancelled"])
        again = reserve_verified_listing(
            listing_id=listing["listing_id"], buyer_user_id="303",
            engine=self.engine, now=150,
        )
        self.assertEqual(again["status"], "active")



    def test_buyer_cancel_requires_fresh_escrow_proof_before_relisting(self):
        listing = self.listing()
        self.mark_deposit_pending(listing)
        confirm_nft_escrow_deposit(
            listing_id=listing["listing_id"], tx_hash="ab" * 32,
            event_index=0, verified_escrow_owner="EQescrow",
            expected_escrow_owner="EQescrow", evidence_hash="b" * 64,
            provider_ids=["tonapi", "toncenter"], engine=self.engine, now=120,
        )
        reservation = reserve_verified_listing(
            listing_id=listing["listing_id"], buyer_user_id="202",
            engine=self.engine, now=130,
        )
        with self.engine.begin() as conn:
            conn.execute(text(
                "UPDATE marketplace_assets SET ownership_expires_at=140 WHERE asset_id=:id"
            ), {"id": self.asset["asset_id"]})
        cancelled = cancel_listing_reservation(
            reservation_id=reservation["reservation_id"], actor_user_id="202",
            engine=self.engine, now=150,
        )
        self.assertTrue(cancelled["cancelled"])
        with self.engine.connect() as conn:
            status = conn.execute(text(
                "SELECT status FROM marketplace_listings WHERE listing_id=:id"
            ), {"id": listing["listing_id"]}).scalar_one()
        self.assertEqual(status, "pending_verification")
        with self.assertRaises(ValueError):
            reserve_verified_listing(
                listing_id=listing["listing_id"], buyer_user_id="303",
                engine=self.engine, now=160,
            )

    def test_seller_can_cancel_active_listing_before_reservation(self):
        listing = self.listing()
        self.mark_deposit_pending(listing)
        confirm_nft_escrow_deposit(
            listing_id=listing["listing_id"], tx_hash="ab" * 32,
            event_index=0, verified_escrow_owner="EQescrow",
            expected_escrow_owner="EQescrow", evidence_hash="b" * 64,
            provider_ids=["tonapi", "toncenter"], engine=self.engine, now=120,
        )
        result = cancel_seller_listing(
            listing_id=listing["listing_id"], seller_user_id="101",
            engine=self.engine, now=130,
        )
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["status"], "cancelled")
        duplicate = cancel_seller_listing(
            listing_id=listing["listing_id"], seller_user_id="101",
            engine=self.engine, now=140,
        )
        self.assertTrue(duplicate["duplicate"])

    def test_seller_cannot_cancel_reserved_listing_or_another_sellers_listing(self):
        listing = self.listing()
        self.mark_deposit_pending(listing)
        confirm_nft_escrow_deposit(
            listing_id=listing["listing_id"], tx_hash="ab" * 32,
            event_index=0, verified_escrow_owner="EQescrow",
            expected_escrow_owner="EQescrow", evidence_hash="b" * 64,
            provider_ids=["tonapi", "toncenter"], engine=self.engine, now=120,
        )
        reserve_verified_listing(
            listing_id=listing["listing_id"], buyer_user_id="202",
            engine=self.engine, now=130,
        )
        with self.assertRaises(PermissionError):
            cancel_seller_listing(
                listing_id=listing["listing_id"], seller_user_id="202",
                engine=self.engine, now=140,
            )
        with self.assertRaises(PermissionError):
            cancel_seller_listing(
                listing_id=listing["listing_id"], seller_user_id="101",
                engine=self.engine, now=140,
            )

    def test_listing_event_log_is_append_only(self):
        listing = self.listing()
        with self.assertRaises(DBAPIError), self.engine.begin() as conn:
            conn.execute(text(
                "UPDATE marketplace_listing_events SET event_type='sold' WHERE listing_id=:id"
            ), {"id": listing["listing_id"]})


if __name__ == "__main__":
    unittest.main()
