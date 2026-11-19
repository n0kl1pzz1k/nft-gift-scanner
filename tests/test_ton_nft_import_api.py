import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from fastapi.testclient import TestClient

import app
import marketplace
import ton_connect
import marketplace_offers


class TonNftImportApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "import.sqlite3")
        self.db_patch = patch.object(marketplace, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.offers_patch = patch.object(marketplace_offers, "DB_PATH", self.db_path)
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

    def test_owner_transfer_requires_fresh_live_chain_owner_confirmation(self):
        address = "0:" + "d" * 64
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO verified_assets
                   (id, source, external_asset_id, owner_telegram_id, title, asset_type,
                    image_url, verification_method, verification_reference_hash,
                    status, created_at, verified_at)
                   VALUES ('asset_old', 'ton_nft', ?, '202', 'Old NFT', 'gift', '',
                           'ton_connect_proof', ?, 'verified', 1, 1)""",
                (address, "f" * 64),
            )
        snapshot = {
            "wallet_address": "0:" + "a" * 64,
            "complete": True,
            "items": [{
                "address": address,
                "wallet_address": "0:" + "a" * 64,
                "title": "Transferred NFT",
                "image_url": "",
            }],
        }
        with patch.object(app, "fetch_wallet_nfts", new=AsyncMock(return_value=snapshot)), \
             patch.object(app, "fetch_nft_owner", new=AsyncMock(return_value={
                 "owner_address": "0:" + "a" * 64, "is_scam": False, "trust": "whitelist"
             })) as live_owner:
            response = self.client.post(
                "/api/marketplace/assets/import-ton",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        live_owner.assert_awaited_once_with(address)
        with marketplace._db() as conn:
            row = conn.execute(
                "SELECT owner_telegram_id FROM verified_assets WHERE id='asset_old'"
            ).fetchone()
            history = conn.execute(
                "SELECT COUNT(*) AS n FROM asset_ownership_history WHERE asset_id='asset_old'"
            ).fetchone()
        self.assertEqual(row["owner_telegram_id"], "101")
        self.assertEqual(history["n"], 1)

    def test_stale_indexer_owner_cannot_transfer_registered_asset(self):
        address = "0:" + "d" * 64
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO verified_assets
                   (id, source, external_asset_id, owner_telegram_id, title, asset_type,
                    image_url, verification_method, verification_reference_hash,
                    status, created_at, verified_at)
                   VALUES ('asset_old', 'ton_nft', ?, '202', 'Old NFT', 'gift', '',
                           'ton_connect_proof', ?, 'verified', 1, 1)""",
                (address, "f" * 64),
            )
        snapshot = {
            "wallet_address": "0:" + "a" * 64,
            "complete": True,
            "items": [{
                "address": address,
                "wallet_address": "0:" + "a" * 64,
                "title": "Possibly transferred NFT",
                "image_url": "",
            }],
        }
        with patch.object(app, "fetch_wallet_nfts", new=AsyncMock(return_value=snapshot)), \
             patch.object(app, "fetch_nft_owner", new=AsyncMock(return_value={
                 "owner_address": "0:" + "c" * 64, "is_scam": False, "trust": "whitelist"
             })) as live_owner:
            response = self.client.post(
                "/api/marketplace/assets/import-ton",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 409)
        live_owner.assert_awaited_once_with(address)
        with marketplace._db() as conn:
            row = conn.execute(
                "SELECT owner_telegram_id, status FROM verified_assets WHERE id='asset_old'"
            ).fetchone()
        self.assertEqual(row["owner_telegram_id"], "202")
        self.assertEqual(row["status"], "verified")

    def test_live_owner_provider_failure_does_not_transfer_registered_asset(self):
        address = "0:" + "d" * 64
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO verified_assets
                   (id, source, external_asset_id, owner_telegram_id, title, asset_type,
                    image_url, verification_method, verification_reference_hash,
                    status, created_at, verified_at)
                   VALUES ('asset_old', 'ton_nft', ?, '202', 'Old NFT', 'gift', '',
                           'ton_connect_proof', ?, 'verified', 1, 1)""",
                (address, "f" * 64),
            )
        snapshot = {
            "wallet_address": "0:" + "a" * 64,
            "complete": True,
            "items": [{
                "address": address,
                "wallet_address": "0:" + "a" * 64,
                "title": "Transferred NFT",
                "image_url": "",
            }],
        }
        with patch.object(app, "fetch_wallet_nfts", new=AsyncMock(return_value=snapshot)), \
             patch.object(app, "fetch_nft_owner", new=AsyncMock(side_effect=httpx.ConnectError("provider unavailable"))):
            response = self.client.post(
                "/api/marketplace/assets/import-ton",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 503)
        with marketplace._db() as conn:
            row = conn.execute(
                "SELECT owner_telegram_id, status FROM verified_assets WHERE id='asset_old'"
            ).fetchone()
        self.assertEqual(row["owner_telegram_id"], "202")
        self.assertEqual(row["status"], "verified")

    def test_complete_empty_scan_revokes_nfts_no_longer_owned(self):
        old_address = "0:" + "e" * 64
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO verified_assets
                   (id, source, external_asset_id, owner_telegram_id, title, asset_type,
                    image_url, verification_method, verification_reference_hash,
                    status, created_at, verified_at)
                   VALUES ('asset_old', 'ton_nft', ?, '101', 'Old NFT', 'gift', '',
                           'ton_connect_proof', ?, 'verified', 1, 1)""",
                (old_address, "f" * 64),
            )
        snapshot = {"wallet_address": "0:" + "a" * 64, "complete": True, "items": []}
        with patch.object(app, "fetch_wallet_nfts", new=AsyncMock(return_value=snapshot)):
            response = self.client.post(
                "/api/marketplace/assets/import-ton",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 200)
        with marketplace._db() as conn:
            row = conn.execute("SELECT status FROM verified_assets WHERE id='asset_old'").fetchone()
        self.assertEqual(row["status"], "revoked")

    def test_partial_scan_never_revokes_unseen_assets(self):
        old_address = "0:" + "e" * 64
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO verified_assets
                   (id, source, external_asset_id, owner_telegram_id, title, asset_type,
                    image_url, verification_method, verification_reference_hash,
                    status, created_at, verified_at)
                   VALUES ('asset_old', 'ton_nft', ?, '101', 'Old NFT', 'gift', '',
                           'ton_connect_proof', ?, 'verified', 1, 1)""",
                (old_address, "f" * 64),
            )
        snapshot = {
            "wallet_address": "0:" + "a" * 64,
            "complete": False,
            "items": [{
                "address": "0:" + "d" * 64,
                "wallet_address": "0:" + "a" * 64,
                "title": "New NFT",
                "image_url": "",
            }],
        }
        with patch.object(app, "fetch_wallet_nfts", new=AsyncMock(return_value=snapshot)):
            response = self.client.post(
                "/api/marketplace/assets/import-ton",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 200)
        with marketplace._db() as conn:
            row = conn.execute("SELECT status FROM verified_assets WHERE id='asset_old'").fetchone()
        self.assertEqual(row["status"], "verified")
        self.assertFalse(response.json()["ownership_scan_complete"])

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
        with patch.object(app, "fetch_wallet_nfts", new=AsyncMock(side_effect=httpx.ConnectError("provider unavailable"))):
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
