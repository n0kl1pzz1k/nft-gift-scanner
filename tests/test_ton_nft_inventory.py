import os
import tempfile
import unittest
from unittest.mock import patch

import httpx

from ton_nft_inventory import (
    fetch_wallet_nfts, fetch_nft_owner, fetch_wallet_nft_history,
    find_confirmed_nft_transfer, _normalise_nft, fragment_asset_type,
)


WALLET = "0:" + "a" * 64
NFT = "0:" + "b" * 64


class TonNftInventoryProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_only_directly_owned_nfts_and_paginates(self):
        calls = []

        def handler(request):
            calls.append(request)
            if request.url.params.get("offset") == "0":
                return httpx.Response(200, json={"nft_items": [{
                    "address": NFT,
                    "index": 7,
                    "owner": {"address": WALLET, "is_scam": False},
                    "metadata": {"name": "Verified TON collectible", "image": "https://cdn.example/nft.png"},
                    "previews": [{"resolution": "256x256", "url": "https://cdn.example/preview.png"}],
                    "verified": True,
                    "trust": "whitelist",
                }]})
            return httpx.Response(200, json={"nft_items": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://tonapi.io")
        result = await fetch_wallet_nfts(WALLET, client=client)
        await client.aclose()
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["address"], NFT)
        self.assertEqual(result["items"][0]["wallet_address"], WALLET)
        self.assertEqual(result["items"][0]["title"], "Verified TON collectible")
        self.assertEqual(result["items"][0]["image_url"], "https://cdn.example/preview.png")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url.params.get("indirect_ownership"), "false")

    async def test_fetches_wallet_nft_transfer_history(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"operations": [{
                "operation": "transfer",
                "utime": 1700000100,
                "lt": 123456,
                "transaction_hash": "ab" * 32,
                "item": {"address": NFT},
                "source": {"address": WALLET},
                "destination": {"address": "0:" + "c" * 64},
            }]})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://tonapi.io")
        with patch.dict(os.environ, {"TONAPI_TOKEN": "test-token"}):
            history = await fetch_wallet_nft_history(WALLET, client=client)
        await client.aclose()
        self.assertEqual(len(history), 1)
        self.assertEqual(calls[0].url.path, f"/v2/accounts/{WALLET}/nfts/history")
        self.assertEqual(calls[0].url.params.get("limit"), "1000")

    async def test_nft_transfer_match_requires_exact_item_wallets_and_time(self):
        destination = "0:" + "c" * 64
        operation = {
            "operation": "transfer",
            "utime": 1700000100,
            "lt": 123456,
            "transaction_hash": "ab" * 32,
            "item": {"address": NFT},
            "source": {"address": WALLET},
            "destination": {"address": destination},
        }
        found = find_confirmed_nft_transfer(
            [operation], item_address=NFT, source_address=WALLET,
            destination_address=destination, not_before=1700000000,
        )
        self.assertEqual(found["transaction_hash"], "ab" * 32)
        self.assertIsNone(find_confirmed_nft_transfer(
            [operation], item_address=NFT, source_address="0:" + "d" * 64,
            destination_address=destination, not_before=1700000000,
        ))
        self.assertIsNone(find_confirmed_nft_transfer(
            [operation], item_address=NFT, source_address=WALLET,
            destination_address=destination, not_before=1700000200,
        ))
        self.assertIsNone(find_confirmed_nft_transfer(
            [{**operation, "transaction_hash": ""}], item_address=NFT,
            source_address=WALLET, destination_address=destination, not_before=1700000000,
        ))

    async def test_rejects_items_not_owned_by_connected_wallet(self):
        item = {
            "address": NFT,
            "owner": {"address": "0:" + "c" * 64, "is_scam": False},
            "metadata": {"name": "Not mine"},
            "trust": "whitelist",
        }
        self.assertIsNone(_normalise_nft(item, WALLET))

    async def test_rejects_blacklisted_or_scam_items(self):
        blacklisted = {
            "address": NFT,
            "owner": {"address": WALLET, "is_scam": False},
            "metadata": {"name": "Bad"},
            "trust": "blacklist",
        }
        scam = {
            "address": NFT,
            "owner": {"address": WALLET, "is_scam": True},
            "metadata": {"name": "Bad"},
            "trust": "whitelist",
        }
        self.assertIsNone(_normalise_nft(blacklisted, WALLET))
        self.assertIsNone(_normalise_nft(scam, WALLET))

    async def test_live_owner_lookup_returns_normalised_chain_owner(self):
        def handler(request):
            return httpx.Response(200, json={
                "address": NFT,
                "owner": {"address": WALLET, "is_scam": False},
                "trust": "whitelist",
            })
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://tonapi.io")
        with patch.dict(os.environ, {"TONAPI_TOKEN": "test-token"}):
            result = await fetch_nft_owner(NFT, client=client)
        await client.aclose()
        self.assertEqual(result["owner_address"], WALLET)
        self.assertFalse(result["is_scam"])
        self.assertEqual(result["trust"], "whitelist")

    async def test_live_owner_lookup_treats_missing_nft_as_unowned(self):
        def handler(request):
            return httpx.Response(404, json={"error": "not found"})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://tonapi.io")
        with patch.dict(os.environ, {"TONAPI_TOKEN": "test-token"}):
            result = await fetch_nft_owner(NFT, client=client)
        await client.aclose()
        self.assertIsNone(result["owner_address"])

    async def test_untrusted_image_schemes_are_not_rendered(self):
        item = {
            "address": NFT,
            "owner": {"address": WALLET, "is_scam": False},
            "metadata": {"name": "Image test", "image": "javascript:alert(1)"},
            "previews": [{"resolution": "512x512", "url": "http://insecure.example/image.png"}],
            "trust": "none",
        }
        result = _normalise_nft(item, WALLET)
        self.assertEqual(result["image_url"], "")


    async def test_fragment_collection_addresses_are_normalised_and_classified(self):
        username_collection = "EQCA14o1-VWhS2efqoh_9M1b_A9DtKTuoqfmkn83AbJzwnPi"
        number_collection = "EQAOQdwdw8kGftJCSFgOErM1mBjYPe4DBPq8-AhF6vr9si5N"
        self.assertEqual(fragment_asset_type(username_collection), "username")
        self.assertEqual(fragment_asset_type(number_collection), "number")
        self.assertEqual(
            fragment_asset_type("0:80d78a35f955a14b679faa887ff4cd5bfc0f43b4a4eea2a7e6927f3701b273c2"),
            "username",
        )
        self.assertIsNone(fragment_asset_type("0:" + "f" * 64))

    async def test_nft_normalisation_preserves_canonical_collection_address(self):
        item = {
            "address": NFT,
            "owner": {"address": WALLET, "is_scam": False},
            "metadata": {"name": "@canonical"},
            "collection": {
                "address": "EQCA14o1-VWhS2efqoh_9M1b_A9DtKTuoqfmkn83AbJzwnPi",
            },
            "trust": "whitelist",
        }
        result = _normalise_nft(item, WALLET)
        self.assertEqual(
            result["collection_address"],
            "0:80d78a35f955a14b679faa887ff4cd5bfc0f43b4a4eea2a7e6927f3701b273c2",
        )

if __name__ == "__main__":
    unittest.main()
