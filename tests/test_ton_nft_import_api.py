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
        self.owner_verify_patch = patch.object(
            app,
            "verify_nft_owner_onchain",
            new=AsyncMock(return_value={
                "verified": True,
                "owner_address": "0:" + "a" * 64,
                "collection_address": None,
                "index": 1,
                "provider": "test",
            }),
        )
        self.owner_verify_patch.start()
        self.client = TestClient(app.app)

    def tearDown(self):
        self.client.close()
        self.owner_verify_patch.stop()
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


    def test_repeated_wallet_import_is_idempotent(self):
        address = "0:" + "d" * 64
        snapshot = {
            "wallet_address": "0:" + "a" * 64,
            "complete": True,
            "items": [{
                "address": address,
                "wallet_address": "0:" + "a" * 64,
                "title": "Same NFT",
                "image_url": "",
            }],
        }
        with patch.object(app, "fetch_wallet_nfts", new=AsyncMock(return_value=snapshot)):
            first = self.client.post(
                "/api/marketplace/assets/import-ton",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
            second = self.client.post(
                "/api/marketplace/assets/import-ton",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["items"][0]["id"], second.json()["items"][0]["id"])
        inventory = self.client.get(
            "/api/marketplace/assets/me",
            headers={"X-Telegram-Init-Data": "verified-session"},
        )
        self.assertEqual(inventory.status_code, 200)
        self.assertEqual(
            sum(item["external_asset_id"] == address for item in inventory.json()["items"]),
            1,
        )

    def test_multiple_verified_wallets_are_imported_as_one_complete_snapshot(self):
        second_wallet = "0:" + "b" * 64
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO verified_wallets
                   (network, address, telegram_id, public_key, state_init_hash,
                    created_at, last_verified_at, revoked_at)
                   VALUES ('-239', ?, '101', ?, ?, 1, 1, NULL)""",
                (second_wallet, "d" * 64, "e" * 64),
            )
        first_address = "0:" + "c" * 64
        second_address = "0:" + "f" * 64
        snapshots = [
            {
                "wallet_address": "0:" + "a" * 64,
                "complete": True,
                "items": [{
                    "address": first_address,
                    "wallet_address": "0:" + "a" * 64,
                    "title": "Wallet A NFT",
                    "image_url": "",
                }],
            },
            {
                "wallet_address": second_wallet,
                "complete": True,
                "items": [{
                    "address": second_address,
                    "wallet_address": second_wallet,
                    "title": "Wallet B NFT",
                    "image_url": "",
                }],
            },
        ]
        with patch.dict(os.environ, {"TONAPI_TOKEN": "test-token"}), \
             patch.object(app, "fetch_wallet_nfts", new=AsyncMock(side_effect=snapshots)) as fetch:
            response = self.client.post(
                "/api/marketplace/assets/import-ton",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["wallets_scanned"], 2)
        self.assertTrue(response.json()["ownership_scan_complete"])
        self.assertEqual(response.json()["imported"], 2)
        self.assertEqual(fetch.await_count, 2)
        inventory = self.client.get(
            "/api/marketplace/assets/me",
            headers={"X-Telegram-Init-Data": "verified-session"},
        )
        self.assertEqual(
            {item["external_asset_id"] for item in inventory.json()["items"]},
            {first_address, second_address},
        )

    def test_failure_on_second_wallet_leaves_registry_unchanged(self):
        second_wallet = "0:" + "b" * 64
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO verified_wallets
                   (network, address, telegram_id, public_key, state_init_hash,
                    created_at, last_verified_at, revoked_at)
                   VALUES ('-239', ?, '101', ?, ?, 1, 1, NULL)""",
                (second_wallet, "d" * 64, "e" * 64),
            )
            conn.execute(
                """INSERT INTO verified_assets
                   (id, source, external_asset_id, owner_telegram_id, title, asset_type,
                    image_url, verification_method, verification_reference_hash,
                    status, created_at, verified_at)
                   VALUES ('asset_existing', 'ton_nft', ?, '101', 'Existing NFT', 'gift', '',
                           'ton_connect_proof', ?, 'verified', 1, 1)""",
                ("0:" + "e" * 64, "f" * 64),
            )
        first_snapshot = {
            "wallet_address": "0:" + "a" * 64,
            "complete": True,
            "items": [{
                "address": "0:" + "c" * 64,
                "wallet_address": "0:" + "a" * 64,
                "title": "New but uncommitted NFT",
                "image_url": "",
            }],
        }
        with patch.dict(os.environ, {"TONAPI_TOKEN": "test-token"}), \
             patch.object(
                 app,
                 "fetch_wallet_nfts",
                 new=AsyncMock(side_effect=[first_snapshot, httpx.ConnectError("second wallet unavailable")]),
             ):
            response = self.client.post(
                "/api/marketplace/assets/import-ton",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 503)
        with marketplace._db() as conn:
            rows = conn.execute(
                "SELECT external_asset_id, status FROM verified_assets WHERE owner_telegram_id='101'"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["external_asset_id"], "0:" + "e" * 64)
        self.assertEqual(rows[0]["status"], "verified")


    def test_regular_ton_nft_import_rejects_stale_indexer_owner_when_contract_disagrees(self):
        wallet = "0:" + "a" * 64
        address = "0:" + "d" * 64
        snapshot = {
            "wallet_address": wallet,
            "complete": True,
            "items": [{
                "address": address,
                "wallet_address": wallet,
                "title": "Index-only NFT",
                "image_url": "",
                "collection_address": "",
            }],
        }
        with patch.object(app, "fetch_wallet_nfts", new=AsyncMock(return_value=snapshot)), \
             patch.object(app, "verify_nft_owner_onchain", new=AsyncMock(return_value={
                 "verified": False,
                 "owner_address": "0:" + "e" * 64,
                 "collection_address": None,
                 "index": 9,
                 "provider": "tonapi_get_method",
             })) as verify:
            response = self.client.post(
                "/api/marketplace/assets/import-ton",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 409)
        verify.assert_awaited_once_with(address, wallet)
        inventory = self.client.get(
            "/api/marketplace/assets/me",
            headers={"X-Telegram-Init-Data": "verified-session"},
        )
        self.assertEqual(inventory.status_code, 200)
        self.assertEqual(inventory.json()["items"], [])

    def test_fragment_username_and_number_import_require_contract_verification(self):
        wallet = "0:" + "a" * 64
        username_address = "0:" + "d" * 64
        number_address = "0:" + "e" * 64
        snapshot = {
            "wallet_address": wallet,
            "complete": True,
            "items": [
                {
                    "address": username_address,
                    "wallet_address": wallet,
                    "title": "@example",
                    "image_url": "",
                    "collection_address": "0:80d78a35f955a14b679faa887ff4cd5bfc0f43b4a4eea2a7e6927f3701b273c2",
                },
                {
                    "address": number_address,
                    "wallet_address": wallet,
                    "title": "+888 1234 5678",
                    "image_url": "",
                    "collection_address": "0:0e41dc1dc3c9067ed24248580e12b3359818d83dee0304fabcf80845eafafdb2",
                },
            ],
        }
        with patch.object(app, "fetch_wallet_nfts", new=AsyncMock(return_value=snapshot)), \
             patch.object(app, "verify_nft_collection_membership", new=AsyncMock(side_effect=[
                 {"verified": True, "block_seqno": 12345},
                 {"verified": True, "block_seqno": 12346},
             ])) as verify:
            response = self.client.post(
                "/api/marketplace/assets/import-ton",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        inventory = self.client.get(
            "/api/marketplace/assets/me",
            headers={"X-Telegram-Init-Data": "verified-session"},
        )
        self.assertEqual(inventory.status_code, 200)
        types = {item["external_asset_id"]: item["asset_type"] for item in inventory.json()["items"]}
        self.assertEqual(types[username_address], "username")
        self.assertEqual(types[number_address], "number")
        self.assertEqual(verify.await_count, 2)
        self.assertEqual(verify.await_args_list[0].args, (
            username_address,
            snapshot["items"][0]["collection_address"],
            wallet,
        ))

    def test_fragment_spoofed_collection_is_rejected_without_registry_mutation(self):
        wallet = "0:" + "a" * 64
        address = "0:" + "d" * 64
        snapshot = {
            "wallet_address": wallet,
            "complete": True,
            "items": [{
                "address": address,
                "wallet_address": wallet,
                "title": "@spoofed",
                "image_url": "",
                "collection_address": "0:80d78a35f955a14b679faa887ff4cd5bfc0f43b4a4eea2a7e6927f3701b273c2",
            }],
        }
        with patch.object(app, "fetch_wallet_nfts", new=AsyncMock(return_value=snapshot)), \
             patch.object(app, "verify_nft_collection_membership", new=AsyncMock(return_value={
                 "verified": False, "block_seqno": 12345
             })):
            response = self.client.post(
                "/api/marketplace/assets/import-ton",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 409)
        inventory = self.client.get(
            "/api/marketplace/assets/me",
            headers={"X-Telegram-Init-Data": "verified-session"},
        )
        self.assertEqual(inventory.status_code, 200)
        self.assertEqual(inventory.json()["items"], [])

    def test_fragment_contract_provider_failure_does_not_import_or_revoke(self):
        wallet = "0:" + "a" * 64
        address = "0:" + "d" * 64
        snapshot = {
            "wallet_address": wallet,
            "complete": True,
            "items": [{
                "address": address,
                "wallet_address": wallet,
                "title": "@offline",
                "image_url": "",
                "collection_address": "0:80d78a35f955a14b679faa887ff4cd5bfc0f43b4a4eea2a7e6927f3701b273c2",
            }],
        }
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO verified_assets
                   (id, source, external_asset_id, owner_telegram_id, title, asset_type,
                    image_url, verification_method, verification_reference_hash,
                    status, created_at, verified_at)
                   VALUES ('asset_existing', 'ton_nft', ?, '101', 'Existing NFT', 'gift', '',
                           'ton_connect_proof', ?, 'verified', 1, 1)""",
                ("0:" + "e" * 64, "f" * 64),
            )
        with patch.object(app, "fetch_wallet_nfts", new=AsyncMock(return_value=snapshot)), \
             patch.object(app, "verify_nft_collection_membership", new=AsyncMock(
                 side_effect=httpx.ConnectError("TON Center unavailable")
             )):
            response = self.client.post(
                "/api/marketplace/assets/import-ton",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 503)
        with marketplace._db() as conn:
            rows = conn.execute(
                "SELECT external_asset_id, status FROM verified_assets WHERE owner_telegram_id='101'"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["external_asset_id"], "0:" + "e" * 64)
        self.assertEqual(rows[0]["status"], "verified")


if __name__ == "__main__":
    unittest.main()
