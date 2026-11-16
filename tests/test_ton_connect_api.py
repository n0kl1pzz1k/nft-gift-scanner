import os
import tempfile
import unittest
from unittest.mock import patch

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

    def test_wallet_challenge_requires_authenticated_telegram_session(self):
        response = self.client.post("/api/ton-proof/payload")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
