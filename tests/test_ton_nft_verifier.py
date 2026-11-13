import os
import unittest
from unittest.mock import patch

from ton_nft_verifier import verify_seller_nft_ownership_async


class FakeProvider:
    def __init__(self, provider_id, state):
        self.provider_id = provider_id
        self.state = state

    async def get_nft_state(self, item_address):
        return dict(self.state)


class TonNftVerifierTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = {
            "item_address": "0:3333333333333333333333333333333333333333333333333333333333333333", "collection_address": "0:2222222222222222222222222222222222222222222222222222222222222222",
            "token_id": "42", "owner_address": "0:1111111111111111111111111111111111111111111111111111111111111111", "initialized": True,
        }
        self.providers = [
            FakeProvider("toncenter", self.state),
            FakeProvider("tonapi", self.state),
        ]
        self.common = {
            "seller_user_id": "700", "wallet_user_id": "700",
            "verified_wallet_address": "0:1111111111111111111111111111111111111111111111111111111111111111", "asset_type": "collectible_username",
            "network": "ton-mainnet", "collection_address": "0:2222222222222222222222222222222222222222222222222222222222222222",
            "token_id": "42", "item_address": "0:3333333333333333333333333333333333333333333333333333333333333333",
            "allowed_collections": {"collectible_username": {"0:2222222222222222222222222222222222222222222222222222222222222222"}},
            "providers": self.providers,
        }
        self.env = patch.dict(os.environ, {"TON_NFT_PROVIDER_IDS": "toncenter,tonapi"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    async def test_two_independent_sources_confirm_seller_ownership(self):
        result = await verify_seller_nft_ownership_async(**self.common)
        self.assertTrue(result["verified"])
        self.assertEqual(result["owner_wallet_address"], "0:1111111111111111111111111111111111111111111111111111111111111111")
        self.assertEqual(result["provider_ids"], ["tonapi", "toncenter"])

    async def test_wallet_proof_does_not_override_different_nft_owner(self):
        self.providers[1].state = {**self.state, "owner_address": "0:4444444444444444444444444444444444444444444444444444444444444444"}
        with self.assertRaises(ValueError):
            await verify_seller_nft_ownership_async(**self.common)

    async def test_unapproved_collection_and_wrong_user_binding_are_rejected(self):
        with self.assertRaises(PermissionError):
            await verify_seller_nft_ownership_async(
                **{**self.common, "allowed_collections": {"collectible_username": set()}}
            )
        with self.assertRaises(PermissionError):
            await verify_seller_nft_ownership_async(
                **{**self.common, "wallet_user_id": "701"}
            )

    async def test_provider_mismatch_blocks_listing_verification(self):
        self.providers[1].state = {**self.state, "token_id": "43"}
        with self.assertRaises(ValueError):
            await verify_seller_nft_ownership_async(**self.common)


if __name__ == "__main__":
    unittest.main()
