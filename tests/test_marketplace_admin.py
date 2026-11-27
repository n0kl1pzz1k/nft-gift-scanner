import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
import marketplace
import marketplace_offers


class MarketplaceAdminConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "admin.sqlite3")
        self.market_db = patch.object(marketplace, "DB_PATH", self.db_path)
        self.offers_db = patch.object(marketplace_offers, "DB_PATH", self.db_path)
        self.market_db.start()
        self.offers_db.start()
        marketplace.init_marketplace_db()
        marketplace_offers.init_marketplace_offers_db()
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO telegram_users
                   (telegram_id, username, first_name, last_name, photo_url,
                    language_code, is_premium, created_at, updated_at)
                   VALUES ('9001','admin','Admin','','','','0',1,1)"""
            )
            conn.execute(
                """INSERT INTO telegram_users
                   (telegram_id, username, first_name, last_name, photo_url,
                    language_code, is_premium, created_at, updated_at)
                   VALUES ('9002','member','Member','','','','0',1,1)"""
            )
            conn.commit()
        self.admin_token = marketplace.create_telegram_session("9001")
        self.member_token = marketplace.create_telegram_session("9002")
        self.env = patch.dict(os.environ, {"ADMIN_TELEGRAM_IDS": "9001", "APP_ENV": "test"})
        self.env.start()
        self.client = TestClient(app.app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.offers_db.stop()
        self.market_db.stop()
        self.temp_dir.cleanup()

    def headers(self, token):
        return {"X-Telegram-Init-Data": token}

    def test_admin_overview_and_operations_queues_are_available(self):
        response = self.client.get("/api/marketplace/admin/overview", headers=self.headers(self.admin_token))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["users"], 2)
        for path in (
            "/api/marketplace/admin/users",
            "/api/marketplace/admin/assets",
            "/api/marketplace/admin/reconciliation",
            "/api/marketplace/admin/deals",
        ):
            result = self.client.get(path, headers=self.headers(self.admin_token))
            self.assertEqual(result.status_code, 200, f"{path}: {result.text}")

    def test_treasury_status_is_read_only_and_fails_closed_without_config(self):
        with patch.dict(os.environ, {
            "HASSLE_TON_MULTISIG_EXPECTED_ADDRESS": "",
            "HASSLE_TON_MULTISIG_CODE_HASH": "",
            "HASSLE_TON_MULTISIG_DATA_HASH": "",
        }, clear=False):
            response = self.client.get(
                "/api/marketplace/admin/treasury",
                headers=self.headers(self.admin_token),
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "configuration_missing")
        self.assertFalse(response.json()["verified"])
        self.assertEqual(response.json()["threshold"], 2)
        self.assertEqual(response.json()["signer_count"], 3)

    def test_treasury_status_requires_finance_or_owner_permission(self):
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_ROLES": "9001:moderator"}):
            response = self.client.get(
                "/api/marketplace/admin/treasury",
                headers=self.headers(self.admin_token),
            )
        self.assertEqual(response.status_code, 403)

    def test_non_admin_cannot_access_operations_console(self):
        response = self.client.get("/api/marketplace/admin/overview", headers=self.headers(self.member_token))
        self.assertEqual(response.status_code, 403)

    def test_admin_role_limits_console_permissions(self):
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_ROLES": "9001:moderator"}):
            role = self.client.get("/api/marketplace/admin/roles/me", headers=self.headers(self.admin_token))
            users = self.client.get("/api/marketplace/admin/users", headers=self.headers(self.admin_token))
            reports = self.client.get("/api/marketplace/admin/reports", headers=self.headers(self.admin_token))
        self.assertEqual(role.status_code, 200, role.text)
        self.assertEqual(role.json()["role"], "moderator")
        self.assertEqual(users.status_code, 403)
        self.assertEqual(reports.status_code, 200, reports.text)

    def test_moderator_can_revoke_verified_asset_and_audit_it(self):
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id="EQAdminModerationAsset",
            owner_telegram_id="9002", title="Moderation test asset",
            asset_type="gift", image_url="",
            verification_method="ton_connect_proof",
            verification_reference="admin-test-reference",
        )
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_ROLES": "9001:moderator"}):
            response = self.client.post(
                f"/api/marketplace/admin/assets/{asset['id']}/revoke",
                headers=self.headers(self.admin_token),
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "revoked")
        with marketplace._db() as conn:
            row = conn.execute("SELECT status, revoked_at FROM verified_assets WHERE id=?", (asset["id"],)).fetchone()
            event = conn.execute(
                "SELECT action, target_type FROM security_audit_events ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(row["status"], "revoked")
        self.assertIsNotNone(row["revoked_at"])
        self.assertEqual(event["action"], "admin_asset_revoked")
        self.assertEqual(event["target_type"], "verified_asset")

    def test_moderator_can_revoke_verified_asset_and_audit_it(self):
        now = 1700000000
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO verified_assets
                   (id, source, external_asset_id, owner_telegram_id, title, asset_type,
                    image_url, verification_method, verification_reference_hash, status,
                    created_at, verified_at, revoked_at)
                   VALUES ('asset-admin-001','telegram_gift','gift-001','9002','Gift',
                           'gift','','telegram_gift_owner',?, 'verified',?,?,NULL)""",
                ("a" * 64, now, now),
            )
            conn.execute(
                """INSERT INTO marketplace_orders
                   (id,user_id,owner_name,kind,title,price,currency,status,created_at,updated_at,note,asset_id)
                   VALUES ('listing-admin-001','9002','Member','listing','Gift','10','TON','active',?,?, '', 'asset-admin-001')""",
                (now, now),
            )
            conn.commit()
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_ROLES": "9001:moderator"}):
            response = self.client.post(
                "/api/marketplace/admin/assets/asset-admin-001/revoke",
                headers=self.headers(self.admin_token),
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["cancelled_listings"], 1)
        with marketplace._db() as conn:
            asset = conn.execute("SELECT status FROM verified_assets WHERE id='asset-admin-001'").fetchone()
            listing = conn.execute("SELECT status FROM marketplace_orders WHERE id='listing-admin-001'").fetchone()
            audit = conn.execute(
                "SELECT action,target_type,metadata_json FROM security_audit_events ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(asset["status"], "revoked")
        self.assertEqual(listing["status"], "cancelled")
        self.assertEqual(audit["action"], "admin_asset_revoked")
        self.assertEqual(audit["target_type"], "verified_asset")
        self.assertIn('"cancelled_listings":1', audit["metadata_json"])

    def test_admin_can_revoke_user_sessions_and_action_is_audited(self):
        target_token = marketplace.create_telegram_session("9002")
        response = self.client.post(
            "/api/marketplace/admin/users/9002/revoke-sessions",
            headers=self.headers(self.admin_token),
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreaterEqual(response.json()["revoked_sessions"], 1)
        self.assertIsNone(marketplace.get_telegram_session_user(target_token))
        with marketplace._db() as conn:
            event = conn.execute(
                """SELECT actor_telegram_id, action, target_type, target_hash
                   FROM security_audit_events ORDER BY created_at DESC LIMIT 1"""
            ).fetchone()
        self.assertEqual(event["actor_telegram_id"], "9001")
        self.assertEqual(event["action"], "telegram_sessions_revoked_all")
        self.assertEqual(event["target_type"], "account")


if __name__ == "__main__":
    unittest.main()
