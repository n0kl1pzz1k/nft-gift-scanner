import os
import tempfile
import sqlite3
import unittest
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
from unittest.mock import AsyncMock, patch
import asyncio

from fastapi.testclient import TestClient

import app as api
import marketplace
import marketplace_offers


class SharedMarketplaceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(marketplace, "DB_PATH", os.path.join(self.temp_dir.name, "test.sqlite3"))
        self.db_patch.start()
        self.offers_db_patch = patch.object(marketplace_offers, "DB_PATH", os.path.join(self.temp_dir.name, "test.sqlite3"))
        self.offers_db_patch.start()
        marketplace.init_marketplace_db()
        marketplace_offers.init_marketplace_offers_db()
        api.init_ton_connect_db()
        self.auth_patch = patch.object(
            api,
            "_verify_telegram_init_data",
            side_effect=lambda value: {
                "id": {"user-a": "101", "user-b": "202", "user-c": "303"}.get(value, "202"),
                "first_name": {"user-a": "Tester A", "user-b": "Tester B", "user-c": "Tester C"}.get(value, "Tester B"),
                "username": "tester",
            },
        )
        self.auth_patch.start()
        self.feature_flag_patch = patch.dict(os.environ, {"APP_ENV": "test", "HASSLE_VERIFIED_ASSETS_ENABLED": "true", "HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "true", "HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "true"})
        self.feature_flag_patch.start()
        self.credential_patch = patch.object(
            api,
            "_authenticate_telegram_credential",
            side_effect=lambda value: (
                api._verify_telegram_init_data(value)
                if value in ("user-a", "user-b", "user-c")
                else marketplace.get_telegram_session_user(value)
            ),
        )
        self.credential_patch.start()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        self.credential_patch.stop()
        self.feature_flag_patch.stop()
        self.auth_patch.stop()
        self.offers_db_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_webhook_secret_is_stable_when_derived_from_bot_token(self):
        with patch.dict(os.environ, {}, clear=True):
            first = api._resolve_webhook_secret("test-bot-token")
            second = api._resolve_webhook_secret("test-bot-token")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_webhook_secret_uses_valid_explicit_configuration(self):
        with patch.dict(os.environ, {"BOT_WEBHOOK_SECRET": "Stable_secret-123"}, clear=True):
            self.assertEqual(api._resolve_webhook_secret("test-bot-token"), "Stable_secret-123")

    def test_webhook_secret_rejects_invalid_explicit_configuration(self):
        with patch.dict(os.environ, {"BOT_WEBHOOK_SECRET": "bad secret"}, clear=True):
            with self.assertRaises(RuntimeError):
                api._resolve_webhook_secret("test-bot-token")

    def test_readiness_probe_confirms_database_is_queryable(self):
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        self.assertEqual(response.json()["dependencies"]["database"], "ok")
        self.assertEqual(response.json()["dependencies"]["database_schema"], "ok")

    def test_readiness_probe_returns_503_when_database_is_unavailable(self):
        with patch("marketplace._db", side_effect=RuntimeError("database unavailable")):
            response = self.client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")
        self.assertEqual(response.json()["dependency"], "database")

    def test_readiness_probe_returns_503_when_critical_schema_is_missing(self):
        with marketplace._db() as connection:
            connection.execute("DROP TABLE verified_wallets")
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["dependency"], "database_schema")
        self.assertIn("verified_wallets", response.json()["missing_tables"])

    def test_readiness_probe_checks_marketplace_offers_and_notifications_schema(self):
        with marketplace._db() as connection:
            connection.execute("DROP TABLE marketplace_notifications")
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["dependency"], "database_schema")
        self.assertIn("marketplace_notifications", response.json()["missing_tables"])

    def test_readiness_probe_returns_503_when_required_column_is_missing(self):
        with marketplace._db() as connection:
            connection.execute("ALTER TABLE verified_wallets RENAME COLUMN address TO wallet_address")
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["dependency"], "database_schema")
        self.assertIn("verified_wallets.address", response.json()["missing_columns"])

    def test_database_initializers_are_idempotent_and_foreign_keys_are_valid(self):
        marketplace.init_marketplace_db()
        marketplace_offers.init_marketplace_offers_db()
        api.init_ton_connect_db()

        with marketplace._db() as connection:
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(violations, [])

        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")

    def test_database_connections_enforce_durability_and_lock_wait_policy(self):
        with marketplace._db() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        with marketplace_offers._db() as conn:
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)

    def test_initdata_replay_protection_is_independent_of_query_parameter_order(self):
        signature = "a" * 64
        first = "auth_date=123&user=%7B%7D&hash=" + signature
        reordered = "hash=" + signature + "&auth_date=123&user=%7B%7D"
        self.assertTrue(marketplace.consume_telegram_init_data(first, "101"))
        self.assertFalse(marketplace.consume_telegram_init_data(reordered, "101"))

    def test_telegram_auth_persists_and_updates_user_profile(self):
        first = self.client.post("/api/auth/telegram", json={"init_data": "user-a"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["user"]["id"], "101")
        token = first.json()["session_token"]
        self.assertTrue(token.startswith("grs_"))
        self.assertEqual(first.json()["expires_in"], 43200)
        current = self.client.get("/api/auth/me", headers={"X-Telegram-Init-Data": token})
        self.assertEqual(current.status_code, 200)
        self.assertTrue(current.json()["authenticated"])
        self.assertEqual(current.json()["user"]["id"], "101")
        self.assertFalse(current.json()["is_admin"])
        replay = self.client.post("/api/auth/telegram", json={"init_data": "user-a"})
        self.assertEqual(replay.status_code, 409)
        session_user = api._authenticate_telegram_credential(token)
        self.assertEqual(session_user["id"], "101")
        self.assertIsNone(marketplace.get_telegram_session_user("grs_invalid-token"))
        with marketplace._db() as conn:
            row = conn.execute("SELECT telegram_id, first_name, username FROM telegram_users WHERE telegram_id = ?", ("101",)).fetchone()
        self.assertEqual(row["first_name"], "Tester A")
        self.assertEqual(row["username"], "tester")
        marketplace.upsert_telegram_user({"id":"101", "first_name":"Updated", "last_name":"Name", "username":"updated_handle", "photo_url":"https://t.me/i/userpic/1.jpg", "language_code":"ru", "is_premium":True})
        with marketplace._db() as conn:
            row = conn.execute("SELECT first_name, last_name, username, is_premium FROM telegram_users WHERE telegram_id = ?", ("101",)).fetchone()
        self.assertEqual(row["first_name"], "Updated")
        self.assertEqual(row["last_name"], "Name")
        self.assertEqual(row["username"], "updated_handle")
        self.assertEqual(row["is_premium"], 1)

    def test_telegram_auth_reports_server_verified_admin_role(self):
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_IDS": "101"}):
            admin = self.client.post("/api/auth/telegram", json={"init_data": "user-a"})
            regular = self.client.post("/api/auth/telegram", json={"init_data": "user-b"})
        self.assertEqual(admin.status_code, 200)
        self.assertTrue(admin.json()["is_admin"])
        self.assertEqual(regular.status_code, 200)
        self.assertFalse(regular.json()["is_admin"])


    def test_test_only_asset_overrides_are_disabled_outside_isolated_tests(self):
        with patch.dict(os.environ, {
            "HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "true",
            "HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "true",
        }, clear=True):
            self.assertFalse(marketplace._test_unverified_assets_allowed())
            self.assertFalse(marketplace._test_ton_owner_recheck_skipped())

        with patch.dict(os.environ, {
            "APP_ENV": "test",
            "RAILWAY_ENVIRONMENT_NAME": "production",
            "HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "true",
            "HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "true",
        }, clear=True):
            self.assertFalse(marketplace._test_unverified_assets_allowed())
            self.assertFalse(marketplace._test_ton_owner_recheck_skipped())

        with patch.dict(os.environ, {
            "APP_ENV": "test",
            "HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "true",
            "HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "true",
        }, clear=True):
            self.assertTrue(marketplace._test_unverified_assets_allowed())
            self.assertTrue(marketplace._test_ton_owner_recheck_skipped())

    def test_production_listing_requires_verified_asset_even_if_feature_flag_is_enabled(self):
        with patch.dict(os.environ, {
            "HASSLE_VERIFIED_ASSETS_ENABLED": "true",
            "HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "false",
        }):
            response = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "title": "Invented gift", "price": "2", "currency": "TON"},
            )
        self.assertEqual(response.status_code, 409)
        self.assertIn("verified asset", response.json()["detail"].lower())

    def test_verified_asset_registration_is_idempotent_and_listing_uses_server_metadata(self):
        attestation = {
            "source": "ton_nft", "external_asset_id": "EQB_verified_asset_001",
            "owner_telegram_id": "101", "title": "Verified Telegram Gift",
            "asset_type": "gift", "image_url": "https://cdn.example/gift.webp",
            "verification_method": "ton_connect_proof",
            "verification_reference": "one-time-server-verification-reference",
        }
        asset = marketplace.register_verified_asset(**attestation)
        repeated = marketplace.register_verified_asset(**attestation)
        self.assertEqual(asset["id"], repeated["id"])
        with patch.dict(os.environ, {"HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "false"}):
            response = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "asset_id": asset["id"], "title": "Seller spoofed title", "price": "2", "currency": "TON"},
            )
        self.assertEqual(response.status_code, 201)
        item = response.json()["item"]
        self.assertEqual(item["title"], "Verified Telegram Gift")
        self.assertEqual(item["asset_type"], "gift")
        self.assertEqual(item["asset_id"], asset["id"])
        self.assertEqual(item["image_url"], "https://cdn.example/gift.webp")

    def test_ton_nft_listing_rechecks_current_owner_against_linked_wallet(self):
        wallet_address = "0:" + "a" * 64
        nft_address = "0:" + "b" * 64
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id=nft_address,
            owner_telegram_id="101", title="Live owner NFT", asset_type="gift",
            image_url="", verification_method="ton_connect_proof",
            verification_reference="one-time-server-verification-reference",
        )
        with patch.dict(os.environ, {"HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "false"}), patch(
            "ton_connect.list_verified_wallets",
            return_value=[{"network": "-239", "address": wallet_address}],
        ), patch(
            "ton_nft_inventory.fetch_nft_owner",
            new=AsyncMock(return_value={"owner_address": wallet_address, "is_scam": False, "trust": "whitelist"}),
        ):
            response = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "asset_id": asset["id"], "title": "Spoof", "price": "2", "currency": "TON"},
            )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["item"]["title"], "Live owner NFT")

    def test_ton_nft_listing_is_rejected_and_asset_revoked_when_owner_changed(self):
        wallet_address = "0:" + "a" * 64
        nft_address = "0:" + "b" * 64
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id=nft_address,
            owner_telegram_id="101", title="Transferred NFT", asset_type="gift",
            image_url="", verification_method="ton_connect_proof",
            verification_reference="one-time-server-verification-reference",
        )
        with patch.dict(os.environ, {"HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "false"}), patch(
            "ton_connect.list_verified_wallets",
            return_value=[{"network": "-239", "address": wallet_address}],
        ), patch(
            "ton_nft_inventory.fetch_nft_owner",
            new=AsyncMock(return_value={"owner_address": "0:" + "c" * 64, "is_scam": False, "trust": "whitelist"}),
        ):
            response = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "asset_id": asset["id"], "title": "Spoof", "price": "2", "currency": "TON"},
            )
        self.assertEqual(response.status_code, 409)
        with marketplace._db() as conn:
            row = conn.execute("SELECT status FROM verified_assets WHERE id = ?", (asset["id"],)).fetchone()
        self.assertEqual(row["status"], "revoked")

    def test_verified_asset_metadata_is_immutable_and_cannot_be_claimed_by_another_owner(self):
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id="EQB_immutable_asset",
            owner_telegram_id="101", title="Immutable gift", asset_type="gift",
            image_url="https://cdn.example/immutable.webp",
            verification_method="ton_connect_proof",
            verification_reference="one-time-server-verification-reference",
        )
        with marketplace._db() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE verified_assets SET title = ? WHERE id = ?", ("Spoofed title", asset["id"]))
        with self.assertRaises(ValueError):
            marketplace.register_verified_asset(
                source="ton_nft", external_asset_id="EQB_immutable_asset",
                owner_telegram_id="202", title="Claimed by B", asset_type="gift",
                image_url="", verification_method="ton_connect_proof",
                verification_reference="different-one-time-verification-reference",
            )

    def test_revoked_ton_nft_can_move_to_new_verified_owner_without_resurrecting_old_listing(self):
        nft_address = "0:" + "b" * 64
        old_asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id=nft_address,
            owner_telegram_id="101", title="NFT before transfer", asset_type="gift",
            image_url="https://cdn.example/before.webp",
            verification_method="ton_connect_proof",
            verification_reference="old-owner-one-time-verification-reference",
        )
        with patch.dict(os.environ, {"HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "true"}):
            listing = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "asset_id": old_asset["id"], "title": "Ignored", "price": "3", "currency": "TON"},
            )
        self.assertEqual(listing.status_code, 201)
        offer = self.client.post(
            "/api/marketplace/offers",
            headers={"X-Telegram-Init-Data": "user-b"},
            json={"order_id": listing.json()["item"]["id"], "amount": "2", "note": "Interested"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        new_asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id=nft_address,
            owner_telegram_id="202", title="NFT after transfer", asset_type="gift",
            image_url="https://cdn.example/after.webp",
            verification_method="ton_connect_proof",
            verification_reference="new-owner-one-time-verification-reference",
            allow_owner_transfer=True,
        )
        self.assertEqual(new_asset["id"], old_asset["id"])
        self.assertEqual(new_asset["owner_telegram_id"], "202")
        self.assertEqual(new_asset["title"], "NFT after transfer")
        with marketplace._db() as conn:
            old_listing = conn.execute(
                "SELECT status FROM marketplace_orders WHERE id = ?", (listing.json()["item"]["id"],)
            ).fetchone()
            history = conn.execute(
                "SELECT previous_owner_id, new_owner_id FROM asset_ownership_history WHERE asset_id = ?",
                (old_asset["id"],),
            ).fetchone()
            old_offer = conn.execute(
                "SELECT status FROM marketplace_offers WHERE id = ?", (offer.json()["item"]["id"],)
            ).fetchone()
        self.assertEqual(old_listing["status"], "cancelled")
        self.assertEqual(old_offer["status"], "declined")
        self.assertEqual(history["previous_owner_id"], "101")
        self.assertEqual(history["new_owner_id"], "202")

    def test_public_catalog_hides_legacy_unverified_listings_but_keeps_verified_assets(self):
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id="EQB_catalog_verified",
            owner_telegram_id="101", title="Verified catalogue gift", asset_type="gift",
            image_url="https://cdn.example/catalog.webp",
            verification_method="ton_connect_proof",
            verification_reference="one-time-server-verification-reference",
        )
        with patch.dict(os.environ, {"HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "false"}):
            verified = self.client.post(
                "/api/marketplace/orders", headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "asset_id": asset["id"], "title": "Ignore this title", "price": "2", "currency": "TON"},
            )
        self.assertEqual(verified.status_code, 201)
        with patch.dict(os.environ, {"HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "true"}):
            legacy = self.client.post(
                "/api/marketplace/orders", headers={"X-Telegram-Init-Data": "user-b"},
                json={"kind": "listing", "title": "Legacy unverified listing", "price": "1", "currency": "TON"},
            )
        self.assertEqual(legacy.status_code, 201)
        with patch.dict(os.environ, {"HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "false"}):
            catalog = self.client.get("/api/marketplace/orders?status=all")
        titles = [item["title"] for item in catalog.json()["items"]]
        self.assertIn("Verified catalogue gift", titles)
        self.assertNotIn("Legacy unverified listing", titles)

    def test_buy_request_does_not_require_owned_asset(self):
        with patch.dict(os.environ, {"HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "false"}):
            response = self.client.post(
                "/api/marketplace/orders", headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "buy-order", "title": "Looking for a collectible gift", "price": "2", "currency": "TON"},
            )
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.json()["item"]["asset_id"])

    def test_verified_inventory_is_private_to_its_telegram_owner(self):
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id="EQB_private_inventory",
            owner_telegram_id="101", title="Private inventory gift", asset_type="gift",
            image_url="https://cdn.example/private.webp",
            verification_method="ton_connect_proof",
            verification_reference="one-time-server-verification-reference",
        )
        owner = self.client.get("/api/marketplace/assets/me", headers={"X-Telegram-Init-Data": "user-a"})
        other = self.client.get("/api/marketplace/assets/me", headers={"X-Telegram-Init-Data": "user-b"})
        anonymous = self.client.get("/api/marketplace/assets/me")
        self.assertEqual(owner.status_code, 200)
        self.assertEqual(owner.json()["items"][0]["id"], asset["id"])
        self.assertEqual(other.json()["items"], [])
        self.assertEqual(anonymous.status_code, 401)

    def test_verified_asset_cannot_have_two_active_listings(self):
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id="EQB_single_listing",
            owner_telegram_id="101", title="One listing only", asset_type="gift",
            image_url="", verification_method="ton_connect_proof",
            verification_reference="one-time-server-verification-reference",
        )
        headers = {"X-Telegram-Init-Data": "user-a"}
        with patch.dict(os.environ, {"HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "false"}):
            first = self.client.post(
                "/api/marketplace/orders", headers=headers,
                json={"kind": "listing", "asset_id": asset["id"], "title": "Ignored", "price": "2", "currency": "TON"},
            )
            second = self.client.post(
                "/api/marketplace/orders", headers=headers,
                json={"kind": "listing", "asset_id": asset["id"], "title": "Ignored", "price": "3", "currency": "TON"},
            )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)

    def test_verified_asset_cannot_be_listed_by_another_telegram_user(self):
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id="EQB_owned_by_a",
            owner_telegram_id="101", title="Owner A gift", asset_type="gift",
            image_url="", verification_method="ton_connect_proof",
            verification_reference="one-time-server-verification-reference",
        )
        with patch.dict(os.environ, {"HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "false"}):
            response = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-b"},
                json={"kind": "listing", "asset_id": asset["id"], "title": "Spoof", "price": "2", "currency": "TON"},
            )
        self.assertEqual(response.status_code, 403)

    def test_listing_and_offer_mutations_are_closed_without_verified_asset_pipeline(self):
        with patch.dict(os.environ, {"HASSLE_VERIFIED_ASSETS_ENABLED": "false"}):
            listing = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "title": "Invented Gift", "price": "2", "currency": "TON"},
            )
            offer = self.client.post(
                "/api/marketplace/offers",
                headers={"X-Telegram-Init-Data": "user-b"},
                json={"order_id": "gr-does-not-exist", "amount": "1"},
            )
        self.assertEqual(listing.status_code, 409)
        self.assertIn("ownership verification", listing.json()["detail"].lower())
        self.assertEqual(offer.status_code, 409)
        self.assertIn("verified inventory", offer.json()["detail"].lower())

    def test_unverified_marketplace_hides_legacy_orders_offers_and_activity(self):
        owner = {"X-Telegram-Init-Data": "user-a"}
        sender = {"X-Telegram-Init-Data": "user-b"}
        with patch.dict(os.environ, {"HASSLE_VERIFIED_ASSETS_ENABLED": "true"}):
            order = self.client.post(
                "/api/marketplace/orders", headers=owner,
                json={"kind": "listing", "title": "Legacy Test Gift", "price": "2", "currency": "TON"},
            )
            self.assertEqual(order.status_code, 201)
            offer = self.client.post(
                "/api/marketplace/offers", headers=sender,
                json={"order_id": order.json()["item"]["id"], "amount": "1"},
            )
            self.assertEqual(offer.status_code, 201)
        with patch.dict(os.environ, {"HASSLE_VERIFIED_ASSETS_ENABLED": "false"}):
            self.assertEqual(self.client.get("/api/marketplace/orders").json()["items"], [])
            self.assertEqual(self.client.get("/api/marketplace/mine", headers=owner).json()["items"], [])
            self.assertEqual(self.client.get("/api/marketplace/offers", headers=sender).json()["items"], [])
            self.assertEqual(self.client.get("/api/marketplace/activity", headers=owner).json()["items"], [])
            self.assertEqual(self.client.get("/api/marketplace/notifications", headers=owner).json()["items"], [])

    def test_orders_are_shared_and_stats_are_server_calculated(self):
        created = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"kind": "listing", "title": "Plush Pepe", "price": "2.5", "currency": "TON"},
        )
        self.assertEqual(created.status_code, 201)
        order_id = created.json()["item"]["id"]
        mine = self.client.get("/api/marketplace/mine", headers={"X-Telegram-Init-Data": "user-a"})
        self.assertEqual(mine.status_code, 200)
        self.assertEqual([item["id"] for item in mine.json()["items"]], [order_id])
        public = self.client.get("/api/marketplace/orders", headers={"X-Telegram-Init-Data": "user-b"})
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json()["total"], 1)
        self.assertEqual(public.json()["items"][0]["id"], order_id)
        self.assertFalse(public.json()["items"][0]["isMine"])
        no_simulation = self.client.post(
            f"/api/marketplace/orders/{order_id}/simulate-fill",
            headers={"X-Telegram-Init-Data": "user-b"},
        )
        self.assertEqual(no_simulation.status_code, 404)
        stats = self.client.get("/api/marketplace/stats")
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.json()["total_orders"], 1)
        self.assertEqual(stats.json()["floor_ton"], 2.5)
        self.assertEqual(stats.json()["floor_stars"], 0)
        unchanged = self.client.get("/api/marketplace/orders?status=active")
        self.assertTrue(any(item["id"] == order_id for item in unchanged.json()["items"]))

    def test_synthetic_fill_route_is_absent_and_owner_can_cancel(self):
        created = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"kind": "buy-order", "title": "Cap", "price": 50, "currency": "XTR"},
        )
        order_id = created.json()["item"]["id"]
        fill = self.client.post(
            f"/api/marketplace/orders/{order_id}/simulate-fill",
            headers={"X-Telegram-Init-Data": "user-a"},
        )
        self.assertEqual(fill.status_code, 404)
        cancelled = self.client.delete(
            f"/api/marketplace/orders/{order_id}",
            headers={"X-Telegram-Init-Data": "user-a"},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["item"]["status"], "cancelled")

    def test_offer_history_pagination_keeps_account_scope(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        for index in range(3):
            order = self.client.post(
                "/api/marketplace/orders", headers=owner_headers,
                json={"kind": "listing", "title": f"Paginated Gift {index}", "price": "1", "currency": "TON"},
            )
            self.assertEqual(order.status_code, 201)
            offered = self.client.post(
                "/api/marketplace/offers", headers=sender_headers,
                json={"order_id": order.json()["item"]["id"], "amount": "1"},
            )
            self.assertEqual(offered.status_code, 201)

        first_page = self.client.get(
            "/api/marketplace/offers?limit=2&offset=0", headers=sender_headers,
        )
        second_page = self.client.get(
            "/api/marketplace/offers?limit=2&offset=2", headers=sender_headers,
        )
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.json()["total"], 3)
        self.assertEqual(len(first_page.json()["items"]), 2)
        self.assertEqual(len(second_page.json()["items"]), 1)
        self.assertNotEqual(
            first_page.json()["items"][0]["id"],
            first_page.json()["items"][1]["id"],
        )

        unrelated = self.client.get(
            "/api/marketplace/offers?limit=10&offset=0",
            headers={"X-Telegram-Init-Data": "user-c"},
        )
        self.assertEqual(unrelated.json()["total"], 0)
        self.assertEqual(unrelated.json()["items"], [])

    def test_offer_acceptance_stays_disabled_until_real_escrow_is_live(self):
        created = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"kind": "listing", "title": "Plush Pepe", "price": "2.5", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        offered = self.client.post(
            "/api/marketplace/offers",
            headers={"X-Telegram-Init-Data": "user-b"},
            json={"order_id": order_id, "amount": "2.2", "note": "offer"},
        )
        self.assertEqual(offered.status_code, 201)
        offer_id = offered.json()["item"]["id"]
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer_id}",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 409)
        self.assertIn("escrow", accepted.json()["detail"].lower())
        offers = self.client.get("/api/marketplace/offers", headers={"X-Telegram-Init-Data": "user-a"})
        self.assertEqual(offers.json()["items"][0]["status"], "pending")
        deals = self.client.get("/api/marketplace/deals", headers={"X-Telegram-Init-Data": "user-b"})
        self.assertEqual(deals.json()["items"], [])
        order = self.client.get("/api/marketplace/orders?status=active")
        self.assertTrue(any(item["id"] == order_id for item in order.json()["items"]))

    def test_offer_creation_rolls_back_when_received_notification_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        order = self.client.post(
            "/api/marketplace/orders",
            headers=owner_headers,
            json={"kind": "listing", "title": "Offer creation rollback", "price": "2", "currency": "TON"},
        )
        self.assertEqual(order.status_code, 201, order.text)
        order_id = order.json()["item"]["id"]

        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_offer_received_notification
                   BEFORE INSERT ON marketplace_notifications
                   WHEN NEW.event_type = 'offer_received'
                   BEGIN SELECT RAISE(ABORT, 'injected offer notification failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.post(
                "/api/marketplace/offers",
                headers=sender_headers,
                json={"order_id": order_id, "amount": "1.8"},
            )

        with marketplace._db() as conn:
            offer_count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_offers WHERE order_id = ?",
                (order_id,),
            ).fetchone()[0]
            notification_count = conn.execute(
                """SELECT COUNT(*) FROM marketplace_notifications
                   WHERE order_id = ? AND event_type = 'offer_received'""",
                (order_id,),
            ).fetchone()[0]
        self.assertEqual(offer_count, 0)
        self.assertEqual(notification_count, 0)

    def test_offer_permissions_and_withdrawal_are_enforced(self):
        created = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"kind": "listing", "title": "Gift", "price": "3", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        own_offer = self.client.post(
            "/api/marketplace/offers",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"order_id": order_id, "amount": "2"},
        )
        self.assertEqual(own_offer.status_code, 403)

        offered = self.client.post(
            "/api/marketplace/offers",
            headers={"X-Telegram-Init-Data": "user-b"},
            json={"order_id": order_id, "amount": "2"},
        )
        offer_id = offered.json()["item"]["id"]
        wrong_actor = self.client.patch(
            f"/api/marketplace/offers/{offer_id}",
            headers={"X-Telegram-Init-Data": "user-b"},
            json={"action": "accept"},
        )
        self.assertEqual(wrong_actor.status_code, 403)

        withdrawn = self.client.patch(
            f"/api/marketplace/offers/{offer_id}",
            headers={"X-Telegram-Init-Data": "user-b"},
            json={"action": "withdraw"},
        )
        self.assertEqual(withdrawn.status_code, 200)
        self.assertEqual(withdrawn.json()["item"]["status"], "withdrawn")

    def test_offer_decline_rolls_back_when_notification_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        order = self.client.post(
            "/api/marketplace/orders",
            headers=owner_headers,
            json={"kind": "listing", "title": "Offer notification rollback", "price": "2", "currency": "TON"},
        )
        self.assertEqual(order.status_code, 201, order.text)
        order_id = order.json()["item"]["id"]
        offer = self.client.post(
            "/api/marketplace/offers",
            headers=sender_headers,
            json={"order_id": order_id, "amount": "1.8"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        offer_id = offer.json()["item"]["id"]

        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_direct_offer_decline_notification
                   BEFORE INSERT ON marketplace_notifications
                   WHEN NEW.event_type = 'offer_declined'
                   BEGIN SELECT RAISE(ABORT, 'injected notification write failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.patch(
                f"/api/marketplace/offers/{offer_id}",
                headers=owner_headers,
                json={"action": "decline"},
            )

        with marketplace._db() as conn:
            offer_status = conn.execute(
                "SELECT status FROM marketplace_offers WHERE id = ?", (offer_id,)
            ).fetchone()["status"]
            declined_notifications = conn.execute(
                """SELECT COUNT(*) FROM marketplace_notifications
                   WHERE offer_id = ? AND event_type = 'offer_declined'""",
                (offer_id,),
            ).fetchone()[0]
        self.assertEqual(offer_status, "pending")
        self.assertEqual(declined_notifications, 0)

    def test_offer_withdrawal_rolls_back_when_notification_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        order = self.client.post(
            "/api/marketplace/orders",
            headers=owner_headers,
            json={"kind": "listing", "title": "Withdraw rollback gift", "price": "2", "currency": "TON"},
        )
        self.assertEqual(order.status_code, 201, order.text)
        order_id = order.json()["item"]["id"]
        offer = self.client.post(
            "/api/marketplace/offers",
            headers=sender_headers,
            json={"order_id": order_id, "amount": "1.8"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        offer_id = offer.json()["item"]["id"]

        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_offer_withdraw_notification
                   BEFORE INSERT ON marketplace_notifications
                   WHEN NEW.event_type = 'offer_withdrawn'
                   BEGIN SELECT RAISE(ABORT, 'injected withdrawal notification failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.patch(
                f"/api/marketplace/offers/{offer_id}",
                headers=sender_headers,
                json={"action": "withdraw"},
            )

        with marketplace._db() as conn:
            offer_status = conn.execute(
                "SELECT status FROM marketplace_offers WHERE id = ?", (offer_id,)
            ).fetchone()["status"]
            withdrawn_notifications = conn.execute(
                """SELECT COUNT(*) FROM marketplace_notifications
                   WHERE offer_id = ? AND event_type = 'offer_withdrawn'""",
                (offer_id,),
            ).fetchone()[0]
        self.assertEqual(offer_status, "pending")
        self.assertEqual(withdrawn_notifications, 0)

    def test_order_creation_rolls_back_when_created_event_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        title = "Order creation rollback gift"

        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_order_created_event
                   BEFORE INSERT ON marketplace_order_events
                   WHEN NEW.event_type = 'created'
                   BEGIN SELECT RAISE(ABORT, 'injected order event failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.post(
                "/api/marketplace/orders",
                headers=owner_headers,
                json={"kind": "listing", "title": title, "price": "2", "currency": "TON"},
            )

        with marketplace._db() as conn:
            order_count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_orders WHERE user_id = '101' AND title = ?",
                (title,),
            ).fetchone()[0]
            event_count = conn.execute(
                """SELECT COUNT(*) FROM marketplace_order_events
                   WHERE order_id IN (
                       SELECT id FROM marketplace_orders WHERE user_id = '101' AND title = ?
                   ) AND event_type = 'created'""",
                (title,),
            ).fetchone()[0]
        self.assertEqual(order_count, 0)
        self.assertEqual(event_count, 0)

    def test_order_owner_can_edit_active_order_fields(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        created = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Original Gift", "price": "2.5", "currency": "TON", "note": "old"},
        )
        self.assertEqual(created.status_code, 201)
        order_id = created.json()["item"]["id"]

        updated = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=owner_headers,
            json={"price": "3.75", "note": "new note"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["item"]["title"], "Original Gift")
        self.assertEqual(updated.json()["item"]["price"], "3.75")
        self.assertEqual(updated.json()["item"]["note"], "new note")
        self.assertGreaterEqual(updated.json()["item"]["updatedAt"], created.json()["item"]["updatedAt"])

    def test_order_edit_rolls_back_when_revision_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        created = self.client.post(
            "/api/marketplace/orders",
            headers=owner_headers,
            json={"kind": "listing", "title": "Edit rollback gift", "price": "2.5", "currency": "TON", "note": "original"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        order_id = created.json()["item"]["id"]

        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_order_revision_insert
                   BEFORE INSERT ON marketplace_order_revisions
                   BEGIN SELECT RAISE(ABORT, 'injected order revision failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.patch(
                f"/api/marketplace/orders/{order_id}",
                headers=owner_headers,
                json={"price": "3.75", "note": "must not persist"},
            )

        with marketplace._db() as conn:
            order = conn.execute(
                "SELECT price, note FROM marketplace_orders WHERE id = ?", (order_id,)
            ).fetchone()
            revision_count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_order_revisions WHERE order_id = ?",
                (order_id,),
            ).fetchone()[0]
        self.assertEqual(order["price"], "2.5")
        self.assertEqual(order["note"], "original")
        self.assertEqual(revision_count, 0)

    def test_order_edits_are_recorded_in_private_history_and_activity(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        other_headers = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Before edit", "price": "2", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        updated = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=owner_headers,
            json={"price": "2.5"},
        )
        self.assertEqual(updated.status_code, 200)

        history = self.client.get("/api/marketplace/history", headers=owner_headers)
        own_order = next(item for item in history.json()["items"] if item["id"] == order_id)
        self.assertEqual([event["type"] for event in own_order["events"]], ["created", "updated"])

        activity = self.client.get("/api/marketplace/activity", headers=owner_headers)
        self.assertEqual(activity.status_code, 200)
        self.assertEqual(activity.json()["total"], 2)
        self.assertIn("order_updated", {item["type"] for item in activity.json()["items"]})

        unrelated = self.client.get("/api/marketplace/activity", headers=other_headers)
        self.assertEqual(unrelated.json()["items"], [])
        self.assertEqual(unrelated.json()["total"], 0)

    def test_deal_cancellation_rolls_back_when_lifecycle_event_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        order = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Rollback deal gift", "price": "2", "currency": "TON"},
        )
        self.assertEqual(order.status_code, 201, order.text)
        order_id = order.json()["item"]["id"]
        offer = self.client.post(
            "/api/marketplace/offers", headers=sender_headers,
            json={"order_id": order_id, "amount": "1.8"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        offer_id = offer.json()["item"]["id"]
        deal_id = "deal-rollback-atomicity"

        with marketplace._db() as conn:
            conn.execute(
                "UPDATE marketplace_orders SET status = 'matched' WHERE id = ?",
                (order_id,),
            )
            conn.execute(
                """INSERT INTO marketplace_deals
                   (id, order_id, offer_id, listing_owner_id, offer_sender_id,
                    agreed_amount, currency, created_at, status)
                   VALUES (?, ?, ?, '101', '202', '1.8', 'TON', ?, 'payment_not_enabled')""",
                (deal_id, order_id, offer_id, int(time.time())),
            )
            conn.execute(
                """CREATE TRIGGER fail_deal_lifecycle_insert
                   BEFORE INSERT ON marketplace_deal_lifecycle_events
                   BEGIN SELECT RAISE(ABORT, 'injected lifecycle write failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.post(
                f"/api/marketplace/deals/{deal_id}/actions",
                headers=sender_headers,
                json={"action": "cancel"},
            )

        with marketplace._db() as conn:
            deal = conn.execute(
                "SELECT status FROM marketplace_deals WHERE id = ?", (deal_id,)
            ).fetchone()
            listing = conn.execute(
                "SELECT status FROM marketplace_orders WHERE id = ?", (order_id,)
            ).fetchone()
            lifecycle_count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_deal_lifecycle_events WHERE deal_id = ?",
                (deal_id,),
            ).fetchone()[0]
        self.assertEqual(deal["status"], "payment_not_enabled")
        self.assertEqual(listing["status"], "matched")
        self.assertEqual(lifecycle_count, 0)

    def test_cancelling_order_declines_pending_offers_and_notifies_senders(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        order = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Cancelled order gift", "price": "2", "currency": "TON"},
        )
        order_id = order.json()["item"]["id"]
        offer = self.client.post(
            "/api/marketplace/offers", headers=sender_headers,
            json={"order_id": order_id, "amount": "1.8"},
        )
        offer_id = offer.json()["item"]["id"]

        cancelled = self.client.delete(f"/api/marketplace/orders/{order_id}", headers=owner_headers)
        self.assertEqual(cancelled.status_code, 200)

        sender_offers = self.client.get("/api/marketplace/offers", headers=sender_headers)
        updated_offer = next(item for item in sender_offers.json()["items"] if item["id"] == offer_id)
        self.assertEqual(updated_offer["status"], "declined")
        notifications = self.client.get("/api/marketplace/notifications", headers=sender_headers)
        self.assertIn("offer_declined", {item["type"] for item in notifications.json()["items"]})

        owner_offers = self.client.get("/api/marketplace/offers", headers=owner_headers)
        self.assertEqual(
            next(item for item in owner_offers.json()["items"] if item["id"] == offer_id)["status"],
            "declined",
        )

    def test_order_cancellation_rolls_back_when_cancelled_event_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        order = self.client.post(
            "/api/marketplace/orders",
            headers=owner_headers,
            json={"kind": "listing", "title": "Order cancellation event rollback", "price": "2", "currency": "TON"},
        )
        self.assertEqual(order.status_code, 201, order.text)
        order_id = order.json()["item"]["id"]

        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_order_cancelled_event
                   BEFORE INSERT ON marketplace_order_events
                   WHEN NEW.event_type = 'cancelled'
                   BEGIN SELECT RAISE(ABORT, 'injected cancellation event failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.delete(
                f"/api/marketplace/orders/{order_id}",
                headers=owner_headers,
            )

        with marketplace._db() as conn:
            order_status = conn.execute(
                "SELECT status FROM marketplace_orders WHERE id = ?", (order_id,)
            ).fetchone()["status"]
            cancelled_event_count = conn.execute(
                """SELECT COUNT(*) FROM marketplace_order_events
                   WHERE order_id = ? AND event_type = 'cancelled'""",
                (order_id,),
            ).fetchone()[0]
        self.assertEqual(order_status, "active")
        self.assertEqual(cancelled_event_count, 0)

    def test_order_cancellation_rolls_back_offer_decline_when_notification_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        order = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Rollback cancellation gift", "price": "2", "currency": "TON"},
        )
        self.assertEqual(order.status_code, 201, order.text)
        order_id = order.json()["item"]["id"]
        offer = self.client.post(
            "/api/marketplace/offers", headers=sender_headers,
            json={"order_id": order_id, "amount": "1.8"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        offer_id = offer.json()["item"]["id"]

        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_offer_decline_notification
                   BEFORE INSERT ON marketplace_notifications
                   WHEN NEW.event_type = 'offer_declined'
                   BEGIN SELECT RAISE(ABORT, 'injected notification write failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.delete(
                f"/api/marketplace/orders/{order_id}",
                headers=owner_headers,
            )

        with marketplace._db() as conn:
            order_status = conn.execute(
                "SELECT status FROM marketplace_orders WHERE id = ?", (order_id,)
            ).fetchone()["status"]
            offer_status = conn.execute(
                "SELECT status FROM marketplace_offers WHERE id = ?", (offer_id,)
            ).fetchone()["status"]
            declined_notifications = conn.execute(
                """SELECT COUNT(*) FROM marketplace_notifications
                   WHERE offer_id = ? AND event_type = 'offer_declined'""",
                (offer_id,),
            ).fetchone()[0]
        self.assertEqual(order_status, "active")
        self.assertEqual(offer_status, "pending")
        self.assertEqual(declined_notifications, 0)

    def test_order_reports_require_auth_and_prevent_duplicates_and_self_reports(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        reporter_headers = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Reportable gift", "price": "2", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]

        missing_auth = self.client.post(
            f"/api/marketplace/orders/{order_id}/reports", json={"reason": "scam"},
        )
        self.assertEqual(missing_auth.status_code, 401)
        self_report = self.client.post(
            f"/api/marketplace/orders/{order_id}/reports",
            headers=owner_headers, json={"reason": "scam"},
        )
        self.assertEqual(self_report.status_code, 403)

        reported = self.client.post(
            f"/api/marketplace/orders/{order_id}/reports",
            headers=reporter_headers,
            json={"reason": "misleading", "details": "  Description does not match the gift.  "},
        )
        self.assertEqual(reported.status_code, 201)
        self.assertEqual(reported.json()["report"]["status"], "open")
        duplicate = self.client.post(
            f"/api/marketplace/orders/{order_id}/reports",
            headers=reporter_headers, json={"reason": "spam"},
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_admin_can_dismiss_or_remove_reported_orders(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        reporter_headers = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Moderated gift", "price": "3", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        report = self.client.post(
            f"/api/marketplace/orders/{order_id}/reports",
            headers=reporter_headers, json={"reason": "scam", "details": "Suspicious listing"},
        )
        report_id = report.json()["report"]["id"]

        def admin_check(value):
            if value != "admin-session":
                raise api.HTTPException(status_code=403, detail="Administrator access only")
            return {"id": "999"}

        with patch.object(api, "_require_admin", side_effect=admin_check):
            forbidden = self.client.get("/api/marketplace/admin/reports", headers=reporter_headers)
            self.assertEqual(forbidden.status_code, 403)
            queue = self.client.get(
                "/api/marketplace/admin/reports",
                headers={"X-Telegram-Init-Data": "admin-session"},
            )
            self.assertEqual(queue.status_code, 200)
            self.assertEqual(queue.json()["total"], 1)
            self.assertEqual(queue.json()["items"][0]["reason"], "scam")

            removed = self.client.patch(
                f"/api/marketplace/admin/reports/{report_id}",
                headers={"X-Telegram-Init-Data": "admin-session"},
                json={"action": "remove_order"},
            )
            self.assertEqual(removed.status_code, 200)
            self.assertTrue(removed.json()["order_removed"])

        mine = self.client.get("/api/marketplace/mine", headers=owner_headers)
        self.assertEqual(mine.json()["items"][0]["status"], "cancelled")
        with marketplace._db() as conn:
            status = conn.execute(
                "SELECT status FROM marketplace_order_reports WHERE id = ?", (report_id,),
            ).fetchone()["status"]
        self.assertEqual(status, "removed")

    def test_order_edit_rejects_non_owner_and_inactive_orders(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        other_headers = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Protected Gift", "price": "2", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        forbidden = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=other_headers,
            json={"title": "Hijacked"},
        )
        self.assertEqual(forbidden.status_code, 403)

        cancelled = self.client.delete(f"/api/marketplace/orders/{order_id}", headers=owner_headers)
        self.assertEqual(cancelled.status_code, 200)
        inactive = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=owner_headers,
            json={"price": "4"},
        )
        self.assertEqual(inactive.status_code, 409)

    def test_order_asset_metadata_cannot_be_edited(self):
        headers = {"X-Telegram-Init-Data": "user-a"}
        created = self.client.post(
            "/api/marketplace/orders", headers=headers,
            json={"kind": "listing", "title": "Original Gift", "price": "2", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        response = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=headers,
            json={"title": "Invented Gift"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("immutable", response.json()["detail"].lower())

    def test_order_edit_validates_payload_and_requires_auth(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        created = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Gift", "price": "2", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        blank = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=owner_headers,
            json={"title": "   "},
        )
        self.assertEqual(blank.status_code, 422)
        invalid_price = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=owner_headers,
            json={"price": "-1"},
        )
        self.assertEqual(invalid_price.status_code, 422)
        empty = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=owner_headers, json={},
        )
        self.assertEqual(empty.status_code, 422)
        unauthenticated = self.client.patch(
            f"/api/marketplace/orders/{order_id}", json={"title": "No auth"},
        )
        self.assertEqual(unauthenticated.status_code, 401)

    def test_mutations_require_telegram_auth(self):
        response = self.client.post(
            "/api/marketplace/orders",
            json={"kind": "listing", "title": "Gift", "price": 1, "currency": "TON"},
        )
        self.assertEqual(response.status_code, 401)


    def test_create_order_rate_limit_is_persistent_and_returns_retry_after(self):
        headers = {"X-Telegram-Init-Data": "user-a"}
        payload = {"kind": "listing", "title": "Gift", "price": 1, "currency": "TON"}
        for _ in range(8):
            response = self.client.post("/api/marketplace/orders", headers=headers, json=payload)
            self.assertEqual(response.status_code, 201)
        limited = self.client.post("/api/marketplace/orders", headers=headers, json=payload)
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)

    def test_offer_status_mutations_are_rate_limited(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        order = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Rate limit offer update", "price": "1", "currency": "TON"},
        )
        self.assertEqual(order.status_code, 201)
        offered = self.client.post(
            "/api/marketplace/offers", headers=sender_headers,
            json={"order_id": order.json()["item"]["id"], "amount": "1"},
        )
        self.assertEqual(offered.status_code, 201)
        offer_id = offered.json()["item"]["id"]

        for index in range(30):
            response = self.client.patch(
                f"/api/marketplace/offers/{offer_id}", headers=owner_headers,
                json={"action": "decline"},
            )
            self.assertIn(response.status_code, (200, 409))
        limited = self.client.patch(
            f"/api/marketplace/offers/{offer_id}", headers=owner_headers,
            json={"action": "decline"},
        )
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)

    def test_create_offer_rate_limit_is_persistent_and_returns_retry_after(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        order_ids = []
        with marketplace._db() as conn:
            now = 1700000000
            for index in range(16):
                order_id = f"gr-rate-{index:02d}"
                conn.execute(
                    """INSERT INTO marketplace_orders
                    (id, user_id, owner_name, kind, title, price, currency, status, created_at, updated_at, note)
                    VALUES (?, '101', 'Tester A', 'listing', ?, '1', 'TON', 'active', ?, ?, '')""",
                    (order_id, f"Gift {index}", now, now),
                )
                order_ids.append(order_id)
        for order_id in order_ids[:15]:
            response = self.client.post(
                "/api/marketplace/offers", headers=sender_headers,
                json={"order_id": order_id, "amount": "1"},
            )
            self.assertEqual(response.status_code, 201)
        limited = self.client.post(
            "/api/marketplace/offers", headers=sender_headers,
            json={"order_id": order_ids[15], "amount": "1"},
        )
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)

    def test_cors_rejects_untrusted_origins(self):
        response = self.client.options(
            "/api/marketplace/orders",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_cors_allows_patch_methods_used_by_marketplace(self):
        response = self.client.options(
            "/api/marketplace/offers/demo-offer",
            headers={
                "Origin": "https://hassle-nft-market.fj7kmzkhn9.workers.dev",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "content-type,x-telegram-init-data",
            },
        )
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "https://hassle-nft-market.fj7kmzkhn9.workers.dev",
        )
        self.assertIn("PATCH", response.headers.get("access-control-allow-methods", ""))

    def test_cors_allows_production_mini_app_origin(self):
        response = self.client.options(
            "/api/marketplace/orders",
            headers={
                "Origin": "https://hassle-nft-market.fj7kmzkhn9.workers.dev",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type,x-telegram-init-data",
            },
        )
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "https://hassle-nft-market.fj7kmzkhn9.workers.dev",
        )


    def test_notification_history_pagination_is_private(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        for index in range(3):
            order = self.client.post(
                "/api/marketplace/orders", headers=owner_headers,
                json={"kind": "listing", "title": f"Notification Page Gift {index}", "price": "1", "currency": "TON"},
            )
            self.assertEqual(order.status_code, 201)
            offered = self.client.post(
                "/api/marketplace/offers", headers=sender_headers,
                json={"order_id": order.json()["item"]["id"], "amount": "1"},
            )
            self.assertEqual(offered.status_code, 201)

        first_page = self.client.get(
            "/api/marketplace/notifications?limit=2&offset=0", headers=owner_headers,
        )
        second_page = self.client.get(
            "/api/marketplace/notifications?limit=2&offset=2", headers=owner_headers,
        )
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.json()["total"], 3)
        self.assertEqual(len(first_page.json()["items"]), 2)
        self.assertEqual(len(second_page.json()["items"]), 1)
        unrelated = self.client.get(
            "/api/marketplace/notifications?limit=10",
            headers={"X-Telegram-Init-Data": "user-c"},
        )
        self.assertEqual(unrelated.json()["total"], 0)
        self.assertEqual(unrelated.json()["items"], [])

    def test_account_notifications_are_private_and_read_state_persists(self):
        headers_a = {"X-Telegram-Init-Data": "user-a"}
        headers_b = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders", headers=headers_a,
            json={"kind": "listing", "title": "Notification gift", "price": "4", "currency": "TON"},
        )
        self.assertEqual(created.status_code, 201)
        offered = self.client.post(
            "/api/marketplace/offers", headers=headers_b,
            json={"order_id": created.json()["item"]["id"], "amount": "3.5"},
        )
        self.assertEqual(offered.status_code, 201)
        owner_feed = self.client.get("/api/marketplace/notifications", headers=headers_a)
        self.assertEqual(owner_feed.status_code, 200)
        self.assertEqual(owner_feed.json()["items"][0]["type"], "offer_received")
        notification_id = owner_feed.json()["items"][0]["id"]
        sender_feed = self.client.get("/api/marketplace/notifications", headers=headers_b)
        self.assertEqual(sender_feed.json()["items"], [])
        forbidden = self.client.patch(
            f"/api/marketplace/notifications/{notification_id}/read", headers=headers_b,
        )
        self.assertEqual(forbidden.status_code, 404)
        marked = self.client.patch(
            f"/api/marketplace/notifications/{notification_id}/read", headers=headers_a,
        )
        self.assertEqual(marked.status_code, 200)
        self.assertEqual(
            self.client.get("/api/marketplace/notifications", headers=headers_a).json()["unread_count"],
            0,
        )
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offered.json()['item']['id']}", headers=headers_a,
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 409)
        self.assertEqual(
            self.client.get("/api/marketplace/deals", headers=headers_b).json()["items"],
            [],
        )
        sender_stats = self.client.get("/api/marketplace/account/stats", headers=headers_b)
        self.assertEqual(sender_stats.status_code, 200)
        self.assertEqual(sender_stats.json()["deals"], 0)

    def test_account_activity_timeline_is_private_and_paginated(self):
        headers_a = {"X-Telegram-Init-Data": "user-a"}
        headers_b = {"X-Telegram-Init-Data": "user-b"}
        headers_c = {"X-Telegram-Init-Data": "user-c"}
        created = self.client.post(
            "/api/marketplace/orders", headers=headers_a,
            json={"kind": "listing", "title": "Timeline gift", "price": "4", "currency": "TON"},
        )
        self.assertEqual(created.status_code, 201)
        order_id = created.json()["item"]["id"]
        offered = self.client.post(
            "/api/marketplace/offers", headers=headers_b,
            json={"order_id": order_id, "amount": "3.5"},
        )
        self.assertEqual(offered.status_code, 201)
        owner_activity = self.client.get("/api/marketplace/activity?limit=10", headers=headers_a)
        self.assertEqual(owner_activity.status_code, 200)
        owner_types = {item["type"] for item in owner_activity.json()["items"]}
        self.assertIn("order_created", owner_types)
        self.assertIn("offer_received", owner_types)
        sender_activity = self.client.get("/api/marketplace/activity?limit=10", headers=headers_b)
        self.assertIn("offer_sent", {item["type"] for item in sender_activity.json()["items"]})
        unrelated = self.client.get("/api/marketplace/activity?limit=10", headers=headers_c)
        self.assertEqual(unrelated.json()["items"], [])

    def test_account_activity_requires_verified_telegram_session(self):
        response = self.client.get("/api/marketplace/activity")
        self.assertEqual(response.status_code, 401)

    def test_account_stats_require_verified_telegram_session(self):
        response = self.client.get("/api/marketplace/account/stats")
        self.assertEqual(response.status_code, 401)



    def test_competing_offer_acceptance_is_blocked_without_escrow(self):
        created = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"kind": "listing", "title": "One of One", "price": "8", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        offer_ids = []
        for user_token, amount in (("user-b", "7"), ("user-c", "6.5")):
            offered = self.client.post(
                "/api/marketplace/offers",
                headers={"X-Telegram-Init-Data": user_token},
                json={"order_id": order_id, "amount": amount},
            )
            self.assertEqual(offered.status_code, 201)
            offer_ids.append(offered.json()["item"]["id"])
        for offer_id in offer_ids:
            response = self.client.patch(
                f"/api/marketplace/offers/{offer_id}",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"action": "accept"},
            )
            self.assertEqual(response.status_code, 409)
        order = self.client.get("/api/marketplace/orders?status=active")
        self.assertTrue(any(item["id"] == order_id for item in order.json()["items"]))
        for user_token in ("user-b", "user-c"):
            offers = self.client.get(
                "/api/marketplace/offers",
                headers={"X-Telegram-Init-Data": user_token},
            )
            own = next(item for item in offers.json()["items"] if item["order_id"] == order_id)
            self.assertEqual(own["status"], "pending")

    def test_deal_history_stays_empty_until_real_escrow_is_enabled(self):
        headers_a = {"X-Telegram-Init-Data": "user-a"}
        headers_b = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders", headers=headers_a,
            json={"kind": "listing", "title": "Escrow gated gift", "price": "4", "currency": "TON"},
        )
        offered = self.client.post(
            "/api/marketplace/offers", headers=headers_b,
            json={"order_id": created.json()["item"]["id"], "amount": "3.5"},
        )
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offered.json()['item']['id']}", headers=headers_a,
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 409)
        self.assertEqual(self.client.get("/api/marketplace/deals", headers=headers_a).json()["items"], [])
        self.assertEqual(self.client.get("/api/marketplace/deals", headers=headers_b).json()["items"], [])


    def test_legacy_table_names_migrate_without_losing_listing_or_event_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = os.path.join(temp_dir, "legacy.sqlite3")
            conn = sqlite3.connect(database)
            conn.execute("""
                CREATE TABLE demo_orders (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, owner_name TEXT NOT NULL,
                    kind TEXT NOT NULL, title TEXT NOT NULL, price TEXT NOT NULL,
                    currency TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL, note TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute(
                "INSERT INTO demo_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("legacy-listing-1", "1001", "seller", "listing", "Real gift", "2",
                 "TON", "active", 10, 10, ""),
            )
            conn.execute("""
                CREATE TABLE demo_order_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL, actor_user_id TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN ('created', 'simulated_fill', 'cancelled')),
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES demo_orders(id)
                )
            """)
            conn.execute(
                "INSERT INTO demo_order_events(order_id, actor_user_id, event_type, created_at) VALUES (?, ?, ?, ?)",
                ("legacy-listing-1", "1001", "simulated_fill", 11),
            )
            conn.commit()
            conn.close()

            with patch.object(marketplace, "DB_PATH", database), patch.object(marketplace_offers, "DB_PATH", database):
                marketplace.init_marketplace_db()
                marketplace_offers.init_marketplace_offers_db()
                with marketplace._db() as migrated:
                    listing = migrated.execute(
                        "SELECT title FROM marketplace_orders WHERE id = ?",
                        ("legacy-listing-1",),
                    ).fetchone()
                    event = migrated.execute(
                        "SELECT event_type FROM marketplace_order_events WHERE order_id = ?",
                        ("legacy-listing-1",),
                    ).fetchone()
                    legacy_table = migrated.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_orders'"
                    ).fetchone()
                    foreign_key_errors = migrated.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(listing["title"], "Real gift")
            self.assertEqual(event["event_type"], "legacy_unverified")
            self.assertIsNone(legacy_table)
            self.assertEqual(foreign_key_errors, [])


    def test_legacy_unverified_events_are_excluded_from_activity_items_and_total(self):
        headers = {"X-Telegram-Init-Data": "user-a"}
        created = self.client.post(
            "/api/marketplace/orders",
            headers=headers,
            json={"kind": "listing", "title": "Legacy activity count", "price": "2", "currency": "TON"},
        )
        self.assertEqual(created.status_code, 201)
        order_id = created.json()["item"]["id"]
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO marketplace_order_events
                   (order_id, actor_user_id, event_type, created_at)
                   VALUES (?, ?, 'legacy_unverified', ?)""",
                (order_id, "101", int(time.time()) + 1),
            )
        response = self.client.get("/api/marketplace/activity?limit=50", headers=headers)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], len(payload["items"]))
        self.assertNotIn("legacy_unverified", {item["type"] for item in payload["items"]})
