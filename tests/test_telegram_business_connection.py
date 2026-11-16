import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
import marketplace


class TelegramBusinessConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            marketplace, "DB_PATH",
            os.path.join(self.temp_dir.name, "business.sqlite3"),
        )
        self.db_patch.start()
        marketplace.init_marketplace_db()
        self.auth_patch = patch.object(
            app, "_authenticate_telegram_credential",
            return_value={"id": "101"},
        )
        self.auth_patch.start()
        self.client = TestClient(app.app)

    def tearDown(self):
        self.client.close()
        self.auth_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_connection_update_persists_only_owner_and_granted_rights(self):
        saved = marketplace.upsert_telegram_business_connection({
            "id": "business-connection-secret",
            "user": {"id": 101},
            "user_chat_id": 101,
            "is_enabled": True,
            "rights": {
                "can_view_gifts_and_stars": True,
                "can_transfer_and_upgrade_gifts": True,
                "can_transfer_stars": False,
            },
        })
        self.assertTrue(saved["connected"])
        self.assertTrue(saved["gift_import_ready"])
        self.assertTrue(saved["gift_transfer_ready"])
        self.assertFalse(saved["can_transfer_stars"])

        # Revoke rights and ensure the latest Telegram update closes access.
        saved = marketplace.upsert_telegram_business_connection({
            "id": "business-connection-secret",
            "user": {"id": 101},
            "user_chat_id": 101,
            "is_enabled": True,
            "rights": {"can_view_gifts_and_stars": True},
        })
        self.assertFalse(saved["gift_transfer_ready"])

        saved = marketplace.upsert_telegram_business_connection({
            "id": "business-connection-secret",
            "user": {"id": 101},
            "user_chat_id": 101,
            "is_enabled": False,
            "rights": {},
        })
        self.assertFalse(saved["connected"])
        self.assertFalse(saved["gift_import_ready"])

    def test_invalid_owner_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            marketplace.upsert_telegram_business_connection({
                "id": "connection",
                "user": {"id": "not-a-telegram-id"},
                "user_chat_id": 101,
                "is_enabled": True,
                "rights": {},
            })

    def test_authenticated_status_endpoint_does_not_expose_connection_identifier(self):
        marketplace.upsert_telegram_business_connection({
            "id": "private-connection-id",
            "user": {"id": 101},
            "user_chat_id": 101,
            "is_enabled": True,
            "rights": {
                "can_view_gifts_and_stars": True,
                "can_transfer_and_upgrade_gifts": False,
            },
        })
        response = self.client.get(
            "/api/telegram-business/status",
            headers={"X-Telegram-Init-Data": "verified-session"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["gift_import_ready"])
        self.assertFalse(response.json()["gift_transfer_ready"])
        self.assertNotIn("business_connection_id", response.json())
        self.assertNotIn("private-connection-id", response.text)

    def test_bot_webhook_subscribes_to_business_connection_updates(self):
        from bot import create_dispatcher
        dispatcher = create_dispatcher()
        self.assertIn("business_connection", dispatcher.resolve_used_update_types())

    def test_missing_connection_returns_closed_permissions(self):
        response = self.client.get(
            "/api/telegram-business/status",
            headers={"X-Telegram-Init-Data": "verified-session"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["connected"])
        self.assertFalse(response.json()["gift_transfer_ready"])


if __name__ == "__main__":
    unittest.main()
