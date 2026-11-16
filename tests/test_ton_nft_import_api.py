import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app
import marketplace
import ton_connect
import demo_offers


class TonNftImportApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "import.sqlite3")
        self.db_patch = patch.object(marketplace, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.offers_patch = patch.object(demo_offers, "DB_PATH", self.db_path)
        self.offers_patch.start()
        marketplace.init_marketplace_db()
        ton_connect.init_ton_connect_db()
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO verified_wallets
                   (network, address, telegram_id, public_key, state_init_hash,
                    created_at, last_verified_at, revoked_at)
                   VALUES ('-239', ?, '101', ?, ?, 1, 1, NULL)""",
                ("0:" + "a" * 64, "b" * 64, "c" * 64),
            )
        self.auth_patch = patch.object(app, "_authenticate_telegram_credential", return_value={"id": "101"})
        self.auth_patch.start()
        self.client = TestClient(app.app)

    def tearDown(self):
        self.client.close()
        self.auth_patch.stop()
        self.offers_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_import_requires_verified_mainnet_wallet(self):
        with marketplace._db() as conn:
            conn.execute("DELETE FROM verified_wallets")
        response = self.client.post(
            "/api/marketplace/assets/import-ton",
            headers={"X-Telegram-Init-Data": "verified-session"},
        )
        self.assertEqual(response.status_code, 400)

    def test_import_registers_only_provider_returned_owned_nfts(self):
        address = "0:" + "d" * 64
        snapshot = {
            "wallet_address": "0:" + "a" * 64,
            "complete": True,
            "items": [{
                "address": address,
                "wallet_address": "0:" + "a" * 64,
                "title": "Real chain NFT",
                "image_url": "https://cdn.example/real-nft.png",
            }],
        }
        with patch.object(app, "fetch_wallet_nfts", new=AsyncMock(return_value=snapshot)):
            response = self.client.post(
                "/api/marketplace/assets/import-ton",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["imported"], 1)
        inventory = self.client.get(
            "/api/marketplace/assets/me",
            headers={"X-Telegram-Init-Data": "verified-session"},
        )
        self.assertEqual(inventory.status_code, 200)
        self.assertEqual(inventory.json()["items"][0]["title"], "Real chain NFT")
        self.assertEqual(inventory.json()["items"][0]["external_asset_id"], address)

    def test_failed_provider_scan_does_not_change_ownership_registry(self):
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO verified_assets
                   (id, source, external_asset_id, owner_telegram_id, title, asset_type,
                    image_url, verification_method, verification_reference_hash,
                    status, created_at, verified_at)
                   VALUES ('asset_old', 'ton_nft', ?, '101', 'Old NFT', 'gift', '',
                           'ton_connect_proof', ?, 'verified', 1, 1)""",
                ("0:" + "e" * 64, "f" * 64),
            )
        with patch.object(app, "fetch_wallet_nfts", new=AsyncMock(side_effect=RuntimeError("provider unavailable"))):
            response = self.client.post(
                "/api/marketplace/assets/import-ton",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 503)
        with marketplace._db() as conn:
            row = conn.execute("SELECT status FROM verified_assets WHERE id='asset_old'").fetchone()
        self.assertEqual(row["status"], "verified")


if __name__ == "__main__":
    unittest.main()
