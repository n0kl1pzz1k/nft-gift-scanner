import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app
import marketplace
import ton_connect
import demo_offers


class TelegramGiftImportApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "telegram-gifts.sqlite3")
        self.db_patch = patch.object(marketplace, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.offers_patch = patch.object(demo_offers, "DB_PATH", self.db_path)
        self.offers_patch.start()
        marketplace.init_marketplace_db()
        ton_connect.init_ton_connect_db()
        self.auth_patch = patch.object(app, "_authenticate_telegram_credential", return_value={"id": "101"})
        self.auth_patch.start()
        self.client = TestClient(app.app)

    def tearDown(self):
        self.client.close()
        self.auth_patch.stop()
        self.offers_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_import_registers_only_unique_gifts_returned_by_telegram(self):
        snapshot = {
            "complete": True,
            "total_count": 2,
            "supported_unique_count": 1,
            "items": [{
                "external_asset_id": "PlushPepe-42",
                "title": "Plush Pepe #42",
                "asset_type": "gift",
                "image_url": "",
                "gift_url": "https://t.me/nft/PlushPepe-42",
                "send_date": "1760000000",
            }],
        }
        with patch.object(app, "fetch_user_telegram_gifts", new=AsyncMock(return_value=snapshot)):
            response = self.client.post(
                "/api/marketplace/assets/import-telegram",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["imported"], 1)
        inventory = self.client.get(
            "/api/marketplace/assets/me",
            headers={"X-Telegram-Init-Data": "verified-session"},
        )
        self.assertEqual(inventory.status_code, 200)
        self.assertEqual(inventory.json()["items"][0]["source"], "telegram_gift")
        self.assertEqual(inventory.json()["items"][0]["external_asset_id"], "PlushPepe-42")

    def test_failed_telegram_scan_does_not_revoke_existing_gifts(self):
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO verified_assets
                   (id, source, external_asset_id, owner_telegram_id, title, asset_type,
                    image_url, verification_method, verification_reference_hash,
                    status, created_at, verified_at)
                   VALUES ('gift_old', 'telegram_gift', 'OldGift-7', '101', 'Old Gift #7',
                           'gift', '', 'telegram_gift_owner', ?, 'verified', 1, 1)""",
                ("f" * 64,),
            )
        with patch.object(app, "fetch_user_telegram_gifts", new=AsyncMock(side_effect=ValueError("Telegram unavailable"))):
            response = self.client.post(
                "/api/marketplace/assets/import-telegram",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 503)
        with marketplace._db() as conn:
            row = conn.execute("SELECT status FROM verified_assets WHERE id='gift_old'").fetchone()
        self.assertEqual(row["status"], "verified")


if __name__ == "__main__":
    unittest.main()
