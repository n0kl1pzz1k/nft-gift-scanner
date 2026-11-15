import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text

from marketplace_inventory import (
    init_inventory_schema, record_wallet_owned_nft, create_escrow_pending_listing,
    verify_and_activate_escrow_listing,
)


class FakeEscrowProvider:
    def __init__(self, provider_id, state, transfer):
        self.provider_id = provider_id
        self.state = state
        self.transfer = transfer

    async def get_account_code_hash(self, account_address):
        return "b" * 64

    async def get_nft_state(self, item_address):
        return dict(self.state)

    async def get_nft_transfer(self, item_address, old_owner, new_owner):
        return dict(self.transfer)


class NftEscrowMonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name)/'escrow.sqlite3'}", future=True)
        init_inventory_schema(self.engine)
        self.seller = "0:" + "11" * 32
        self.collection = "0:" + "22" * 32
        self.item = "0:" + "33" * 32
        self.escrow = "0:" + "44" * 32
        evidence = {
            "verified": True, "owner_wallet_address": self.seller,
            "collection_address": self.collection, "token_id": "7",
            "item_address": self.item, "evidence_hash": "a" * 64,
        }
        asset = record_wallet_owned_nft(
            user_id="101", wallet_address=self.seller, asset_type="ton_nft",
            collection_address=self.collection, token_id="7", item_address=self.item,
            evidence=evidence, engine=self.engine, now=100,
        )
        self.asset_id = asset["asset_id"]
        listing = create_escrow_pending_listing(
            user_id="101", asset_id=self.asset_id, price_raw=100,
            currency="TON", idempotency_key="escrow-listing-test-0001",
            engine=self.engine, now=110,
        )
        self.listing_id = listing["listing_id"]
        # Monitor tests start after the wallet has submitted the NFT transfer.
        with self.engine.begin() as conn:
            conn.execute(text(
                "UPDATE marketplace_listings SET status='pending_verification' "
                "WHERE listing_id=:id AND status='draft'"
            ), {"id": self.listing_id})
        self.state = {
            "item_address": self.item, "collection_address": self.collection,
            "token_id": "7", "owner_address": self.escrow, "initialized": True,
        }
        self.transfer = {"tx_hash": "ab" * 32, "event_index": 0}
        self.providers = [
            FakeEscrowProvider("tonapi", self.state, self.transfer),
            FakeEscrowProvider("toncenter", self.state, self.transfer),
        ]
        self.env = patch.dict(os.environ, {"GIFT_RADAR_ESCROW_ADDRESS": self.escrow, "GIFT_RADAR_ESCROW_CODE_HASH": "b" * 64})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.engine.dispose()
        self.temp.cleanup()

    async def test_listing_activates_only_after_matching_chain_transfer_and_owner(self):
        result = await verify_and_activate_escrow_listing(
            listing_id=self.listing_id, engine=self.engine, providers=self.providers,
        )
        self.assertTrue(result["activated"])
        self.assertEqual(result["status"], "active")
        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT custody_state, owner_wallet_address FROM marketplace_assets WHERE asset_id=:id"
            ), {"id": self.asset_id}).one()
            proof_count = conn.execute(text(
                "SELECT COUNT(*) FROM marketplace_ownership_proofs WHERE asset_id=:id"
            ), {"id": self.asset_id}).scalar_one()
        self.assertEqual(row.custody_state, "escrowed")
        self.assertEqual(row.owner_wallet_address, self.escrow)
        self.assertEqual(proof_count, 2)

    async def test_provider_disagreement_or_wrong_escrow_owner_blocks_publication(self):
        self.providers[1].transfer = {"tx_hash": "different", "event_index": 0}
        with self.assertRaises(PermissionError):
            await verify_and_activate_escrow_listing(
                listing_id=self.listing_id, engine=self.engine, providers=self.providers,
            )
        self.providers[1].transfer = dict(self.transfer)
        self.providers[1].state = {**self.state, "owner_address": self.seller}
        with self.assertRaises(PermissionError):
            await verify_and_activate_escrow_listing(
                listing_id=self.listing_id, engine=self.engine, providers=self.providers,
            )

    async def test_single_provider_cannot_activate_listing(self):
        with self.assertRaises(PermissionError):
            await verify_and_activate_escrow_listing(
                listing_id=self.listing_id, engine=self.engine, providers=self.providers[:1],
            )


if __name__ == "__main__":
    unittest.main()
