import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app
import marketplace
import ton_connect


class TonConnectApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(marketplace, "DB_PATH", os.path.join(self.temp_dir.name, "api.sqlite3"))
        self.db_patch.start()
        marketplace.init_marketplace_db()
        ton_connect.init_ton_connect_db()
        marketplace.upsert_telegram_user({"id": "101", "first_name": "Wallet owner"})
        self.session = marketplace.create_telegram_session("101")
        self.client = TestClient(app.app)

    def tearDown(self):
        self.client.close()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_authenticated_user_can_issue_challenge_and_link_verified_wallet_once(self):
        headers = {"X-Telegram-Init-Data": self.session}
        issued = self.client.post("/api/ton-proof/payload", headers=headers)
        self.assertEqual(issued.status_code, 200)
        challenge = issued.json()["payload"]
        self.assertEqual(issued.json()["expires_in"], 300)

        verified = {
            "address": "0:" + "a" * 64,
            "network": "-239",
            "public_key": "b" * 64,
            "state_init_hash": "c" * 64,
            "payload": challenge,
        }
        request = {
            "address": verified["address"],
            "network": "-239",
            "public_key": verified["public_key"],
            "state_init": "c3RhdGU=",
            "proof": {
                "timestamp": 1800000000,
                "domain": {"lengthBytes": 40, "value": "hassle-nft-market.fj7kmzkhn9.workers.dev"},
                "signature": "c2ln",
                "payload": challenge,
            },
        }
        with patch.dict(os.environ, {"TON_CONNECT_DOMAIN": "hassle-nft-market.fj7kmzkhn9.workers.dev"}), patch.object(
            app, "verify_ton_proof", return_value=verified
        ):
            response = self.client.post("/api/ton-proof/verify", headers=headers, json=request)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["verified"])
            replay = self.client.post("/api/ton-proof/verify", headers=headers, json=request)
            self.assertEqual(replay.status_code, 409)

        wallets = self.client.get("/api/ton-proof/wallets/me", headers=headers)
        self.assertEqual(wallets.status_code, 200)
        self.assertEqual(len(wallets.json()["items"]), 1)
        self.assertEqual(wallets.json()["items"][0]["address"], verified["address"])

        with marketplace._db() as conn:
            audit = conn.execute(
                "SELECT action, actor_telegram_id, target_type, metadata_json FROM security_audit_events "
                "WHERE action='ton_wallet_linked' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(audit["action"], "ton_wallet_linked")
        self.assertEqual(audit["actor_telegram_id"], "101")
        self.assertEqual(audit["target_type"], "wallet")
        self.assertEqual(__import__("json").loads(audit["metadata_json"]), {"network": "-239"})

    def test_ton_proof_challenge_is_rate_limited_per_account(self):
        with patch.object(app, "_enforce_user_rate_limit") as limiter:
            response = self.client.post(
                "/api/ton-proof/payload",
                headers={"X-Telegram-Init-Data": self.session},
            )
        self.assertEqual(response.status_code, 200, response.text)
        limiter.assert_called_once_with("101", "ton_proof_payload", 10, 60)

    def test_ton_proof_verification_is_rate_limited_before_signature_work(self):
        challenge = self.client.post(
            "/api/ton-proof/payload",
            headers={"X-Telegram-Init-Data": self.session},
        ).json()["payload"]
        request = {
            "address": "0:" + "a" * 64,
            "network": "-239",
            "public_key": "b" * 64,
            "state_init": "c3RhdGU=",
            "proof": {
                "timestamp": 1800000000,
                "domain": {"lengthBytes": 40, "value": "hassle-nft-market.fj7kmzkhn9.workers.dev"},
                "signature": "c2ln",
                "payload": challenge,
            },
        }
        verified = {
            "address": request["address"],
            "network": "-239",
            "public_key": request["public_key"],
            "state_init_hash": "c" * 64,
            "payload": challenge,
        }
        with patch.object(app, "_enforce_user_rate_limit") as limiter, \
             patch.object(app, "verify_ton_proof", return_value=verified), \
             patch.object(app, "consume_challenge_and_link_wallet", return_value=True):
            response = self.client.post(
                "/api/ton-proof/verify",
                headers={"X-Telegram-Init-Data": self.session},
                json=request,
            )
        self.assertEqual(response.status_code, 200, response.text)
        limiter.assert_called_once_with("101", "ton_proof_verify", 10, 60)

    def test_wallet_challenge_requires_authenticated_telegram_session(self):
        response = self.client.post("/api/ton-proof/payload")
        self.assertEqual(response.status_code, 401)

    def test_wallet_can_only_be_revoked_by_its_linked_telegram_account(self):
        address = "0:" + "a" * 64
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO verified_wallets
                   (network, address, telegram_id, public_key, state_init_hash,
                    created_at, last_verified_at, revoked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
                ("-239", address, "101", "b" * 64, "c" * 64, 1, 1),
            )
        marketplace.upsert_telegram_user({"id": "202", "first_name": "Other account"})
        other_session = marketplace.create_telegram_session("202")
        other = self.client.delete(
            f"/api/ton-proof/wallets/-239/{address}",
            headers={"X-Telegram-Init-Data": other_session},
        )
        self.assertEqual(other.status_code, 404)
        self.assertEqual(len(ton_connect.list_verified_wallets("101")), 1)

        owner = self.client.delete(
            f"/api/ton-proof/wallets/-239/{address}",
            headers={"X-Telegram-Init-Data": self.session},
        )
        self.assertEqual(owner.status_code, 200, owner.text)
        self.assertEqual(owner.json(), {"revoked": True})
        self.assertEqual(ton_connect.list_verified_wallets("101"), [])
        with marketplace._db() as conn:
            audit = conn.execute(
                "SELECT action, actor_telegram_id, target_type, metadata_json FROM security_audit_events "
                "WHERE action='ton_wallet_revoked' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(audit["action"], "ton_wallet_revoked")
        self.assertEqual(audit["actor_telegram_id"], "101")
        self.assertEqual(audit["target_type"], "wallet")
        self.assertEqual(__import__("json").loads(audit["metadata_json"]), {"network": "-239"})

        repeated = self.client.delete(
            f"/api/ton-proof/wallets/-239/{address}",
            headers={"X-Telegram-Init-Data": self.session},
        )
        self.assertEqual(repeated.status_code, 404)

    def test_wallet_revocation_and_relink_preserve_and_restore_same_inventory_asset(self):
        headers = {"X-Telegram-Init-Data": self.session}
        wallet_address = "0:" + "a" * 64
        nft_address = "0:" + "d" * 64
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO verified_wallets
                   (network, address, telegram_id, public_key, state_init_hash,
                    created_at, last_verified_at, revoked_at)
                   VALUES ('-239', ?, '101', ?, ?, 1, 1, NULL)""",
                (wallet_address, "b" * 64, "c" * 64),
            )
        asset = marketplace.register_verified_asset(
            source="ton_nft",
            external_asset_id=nft_address,
            owner_telegram_id="101",
            title="Persistent TON NFT",
            asset_type="gift",
            image_url="",
            verification_method="ton_connect_proof",
            verification_reference="ton-contract-owner-v1:test:owner:collection:1",
        )

        revoked = self.client.delete(
            f"/api/ton-proof/wallets/-239/{wallet_address}",
            headers=headers,
        )
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(ton_connect.list_verified_wallets("101"), [])

        inventory_after_revoke = self.client.get("/api/marketplace/assets/me", headers=headers)
        self.assertEqual(inventory_after_revoke.status_code, 200)
        self.assertEqual(
            [item["id"] for item in inventory_after_revoke.json()["items"]],
            [asset["id"]],
        )

        snapshot = {
            "wallet_address": wallet_address,
            "complete": True,
            "items": [{
                "address": nft_address,
                "wallet_address": wallet_address,
                "title": "Persistent TON NFT",
                "image_url": "",
                "collection_address": "",
            }],
        }
        with patch.object(app, "fetch_wallet_nfts", new=AsyncMock(return_value=snapshot)) as fetch:
            unavailable = self.client.post("/api/marketplace/assets/import-ton", headers=headers)
        self.assertEqual(unavailable.status_code, 400)
        fetch.assert_not_called()

        issued = self.client.post("/api/ton-proof/payload", headers=headers)
        self.assertEqual(issued.status_code, 200, issued.text)
        challenge = issued.json()["payload"]
        verified = {
            "address": wallet_address,
            "network": "-239",
            "public_key": "b" * 64,
            "state_init_hash": "c" * 64,
            "payload": challenge,
        }
        request = {
            "address": wallet_address,
            "network": "-239",
            "public_key": "b" * 64,
            "state_init": "c3RhdGU=",
            "proof": {
                "timestamp": 1800000000,
                "domain": {"lengthBytes": 40, "value": "hassle-nft-market.fj7kmzkhn9.workers.dev"},
                "signature": "c2ln",
                "payload": challenge,
            },
        }
        with patch.dict(os.environ, {"TON_CONNECT_DOMAIN": "hassle-nft-market.fj7kmzkhn9.workers.dev"}), patch.object(
            app, "verify_ton_proof", return_value=verified
        ):
            linked = self.client.post("/api/ton-proof/verify", headers=headers, json=request)
        self.assertEqual(linked.status_code, 200, linked.text)
        self.assertEqual(len(ton_connect.list_verified_wallets("101")), 1)

        with patch.object(app, "fetch_wallet_nfts", new=AsyncMock(return_value=snapshot)), patch.object(
            app, "verify_nft_owner_onchain", new=AsyncMock(return_value={
                "verified": True,
                "owner_address": wallet_address,
                "collection_address": None,
                "index": 42,
                "provider": "test",
            })
        ):
            restored = self.client.post("/api/marketplace/assets/import-ton", headers=headers)
        self.assertEqual(restored.status_code, 200, restored.text)

        inventory_after_relink = self.client.get("/api/marketplace/assets/me", headers=headers)
        self.assertEqual(inventory_after_relink.status_code, 200)
        matching = [
            item for item in inventory_after_relink.json()["items"]
            if item["external_asset_id"] == nft_address
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["id"], asset["id"])
        with marketplace._db() as conn:
            row = conn.execute(
                "SELECT status FROM verified_assets WHERE id = ?", (asset["id"],)
            ).fetchone()
        self.assertEqual(row["status"], "verified")

    def test_wallet_revocation_requires_authenticated_session(self):
        response = self.client.delete(
            "/api/ton-proof/wallets/-239/0:" + "a" * 64,
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
