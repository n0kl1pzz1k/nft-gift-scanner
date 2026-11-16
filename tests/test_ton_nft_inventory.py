import os
import tempfile
import unittest
from unittest.mock import patch

import httpx

from ton_nft_inventory import fetch_wallet_nfts, _normalise_nft


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


if __name__ == "__main__":
    unittest.main()
