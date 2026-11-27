import os
import unittest
from unittest.mock import patch

import httpx

from telegram_gift_inventory import (
    TelegramGiftInventoryError,
    fetch_user_telegram_gifts,
    normalise_owned_gift,
)


class TelegramGiftInventoryTests(unittest.IsolatedAsyncioTestCase):
    def test_only_stable_unique_non_blockchain_gifts_are_imported(self):
        unique = {
            "type": "unique",
            "send_date": 1760000000,
            "gift": {
                "name": "PlushPepe-42",
                "number": 42,
                "base_name": "Plush Pepe",
                "is_from_blockchain": False,
            },
        }
        item = normalise_owned_gift(unique)
        self.assertEqual(item["external_asset_id"], "PlushPepe-42")
        self.assertEqual(item["title"], "Plush Pepe #42")
        self.assertEqual(item["gift_url"], "https://t.me/nft/PlushPepe-42")
        self.assertIsNone(normalise_owned_gift({"type": "regular", "gift": {"id": "1"}}))
        self.assertIsNone(normalise_owned_gift({
            "type": "unique",
            "gift": {"name": "OnChain-1", "number": 1, "base_name": "Gift", "is_from_blockchain": True},
        }))
        self.assertIsNone(normalise_owned_gift({
            "type": "unique",
            "gift": {"name": "Burned-1", "number": 1, "base_name": "Gift", "is_burned": True},
        }))

    def test_preserves_model_symbol_backdrop_rarity_and_palette(self):
        item = normalise_owned_gift({
            "type": "unique",
            "gift": {
                "name": "JollyChimp-93222", "number": 93222, "base_name": "Jolly Chimp",
                "model": {"name": "Neon", "rarity_per_mille": 125, "rarity": "legendary"},
                "symbol": {"name": "Star", "rarity_per_mille": 50},
                "backdrop": {
                    "name": "Midnight", "rarity_per_mille": 25,
                    "colors": {"center_color": 1122867, "edge_color": 4478310},
                },
            },
        })
        self.assertEqual(item["traits"]["model"]["name"], "Neon")
        self.assertEqual(item["traits"]["model"]["rarity"], "legendary")
        self.assertEqual(item["traits"]["symbol"]["name"], "Star")
        self.assertEqual(item["traits"]["backdrop"]["name"], "Midnight")
        self.assertEqual(item["traits"]["backdrop"]["colors"]["center_color"], 1122867)
        self.assertEqual(item["number"], 93222)

    async def test_fetches_and_paginates_user_gifts_through_official_bot_api(self):
        calls = []

        def handler(request):
            calls.append(request)
            if request.url.path.endswith("/getUserGifts"):
                payload = __import__("json").loads(request.content)
                self.assertEqual(payload["user_id"], 101)
                self.assertTrue(payload["exclude_from_blockchain"])
                if payload["offset"] == "":
                    return httpx.Response(200, json={
                        "ok": True,
                        "result": {
                            "total_count": 3,
                            "gifts": [
                                {"type": "unique", "send_date": 1, "gift": {
                                    "name": "Gift-1", "number": 1, "base_name": "Gift"
                                }},
                                {"type": "regular", "send_date": 2, "gift": {"id": "regular-id"}},
                            ],
                            "next_offset": "cursor-2",
                        },
                    })
                return httpx.Response(200, json={
                    "ok": True,
                    "result": {
                        "total_count": 3,
                        "gifts": [{
                            "type": "unique", "send_date": 3, "gift": {
                                "name": "Gift-2", "number": 2, "base_name": "Gift"
                            }
                        }],
                    },
                })
            raise AssertionError("Unexpected Telegram API method")

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.telegram.org",
        )
        with patch.dict(os.environ, {"BOT_TOKEN": "123456:test-token"}):
            result = await fetch_user_telegram_gifts("101", client=client)
        await client.aclose()
        self.assertTrue(result["complete"])
        self.assertEqual(result["total_count"], 3)
        self.assertEqual(result["supported_unique_count"], 2)
        self.assertEqual([item["external_asset_id"] for item in result["items"]], ["Gift-1", "Gift-2"])
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("/bot123456:test-token/getUserGifts" in call.url.path for call in calls))

    async def test_requires_server_bot_token(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TelegramGiftInventoryError):
                await fetch_user_telegram_gifts("101")


if __name__ == "__main__":
    unittest.main()
