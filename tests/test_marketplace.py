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
import demo_offers


class SharedMarketplaceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(marketplace, "DB_PATH", os.path.join(self.temp_dir.name, "test.sqlite3"))
        self.db_patch.start()
        self.offers_db_patch = patch.object(demo_offers, "DB_PATH", os.path.join(self.temp_dir.name, "test.sqlite3"))
        self.offers_db_patch.start()
        marketplace.init_marketplace_db()
        demo_offers.init_demo_offers_db()
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
        self.auth_patch.stop()
        self.offers_db_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

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

    def test_favorites_are_private_persistent_and_removable(self):
        headers_a = {"X-Telegram-Init-Data": "user-a"}
        headers_b = {"X-Telegram-Init-Data": "user-b"}
        gift = {"id": "gift-77", "name": "Plush Pepe", "price": "2.5", "currency": "TON"}
        saved = self.client.put(
            "/api/marketplace/favorites/gift-77",
            headers=headers_a,
            json={"gift": gift},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["saved"])

        own = self.client.get("/api/marketplace/favorites", headers=headers_a)
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.json()["items"], [gift])

        other = self.client.get("/api/marketplace/favorites", headers=headers_b)
        self.assertEqual(other.status_code, 200)
        self.assertEqual(other.json()["items"], [])

        removed = self.client.delete("/api/marketplace/favorites/gift-77", headers=headers_a)
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.client.get("/api/marketplace/favorites", headers=headers_a).json()["items"], [])

    def test_favorite_id_must_match_gift_data_and_is_not_truncated(self):
        headers = {"X-Telegram-Init-Data": "user-a"}
        mismatch = self.client.put(
            "/api/marketplace/favorites/gift-77",
            headers=headers,
            json={"gift": {"id": "gift-88", "name": "Plush Pepe"}},
        )
        self.assertEqual(mismatch.status_code, 422)

        oversized_id = "x" * 161
        oversized = self.client.put(
            f"/api/marketplace/favorites/{oversized_id}",
            headers=headers,
            json={"gift": {"id": oversized_id, "name": "Gift"}},
        )
        self.assertEqual(oversized.status_code, 422)

        oversized_delete = self.client.delete(
            f"/api/marketplace/favorites/{oversized_id}",
            headers=headers,
        )
        self.assertEqual(oversized_delete.status_code, 422)

        self.assertEqual(
            self.client.get("/api/marketplace/favorites", headers=headers).json()["items"],
            [],
        )

    def test_admin_marketplace_visibility_persists_across_database_reinitialization(self):
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_IDS": "101"}):
            headers = {"X-Telegram-Init-Data": "user-a"}
            changed = self.client.post(
                "/api/admin/marketplaces/portals",
                headers=headers,
                json={"enabled": False},
            )
            self.assertEqual(changed.status_code, 200)
            self.assertFalse(changed.json()["enabled"])
            marketplace.init_marketplace_db()
            overview = self.client.get("/api/admin/overview", headers=headers)
            self.assertEqual(overview.status_code, 200)
            self.assertFalse(overview.json()["marketplace_visibility"]["portals"])
            self.assertEqual(overview.json()["settings_persistence"], "persistent_sqlite")
            enabled = self.client.post(
                "/api/admin/marketplaces/portals",
                headers=headers,
                json={"enabled": True},
            )
            self.assertEqual(enabled.status_code, 200)
            self.assertTrue(enabled.json()["enabled"])

    def test_admin_marketplace_settings_reject_non_admin(self):
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_IDS": "999"}):
            response = self.client.post(
                "/api/admin/marketplaces/portals",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"enabled": False},
            )
            self.assertEqual(response.status_code, 403)

    def test_admin_overview_requires_admin_authentication(self):
        missing = self.client.get("/api/admin/overview")
        self.assertEqual(missing.status_code, 401)
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_IDS": "999"}):
            regular = self.client.get(
                "/api/admin/overview",
                headers={"X-Telegram-Init-Data": "user-a"},
            )
        self.assertEqual(regular.status_code, 403)

    def test_admin_rejects_unknown_marketplace(self):
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_IDS": "101"}):
            response = self.client.post(
                "/api/admin/marketplaces/unknown",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"enabled": False},
            )
        self.assertEqual(response.status_code, 404)

    def test_status_reports_persistent_metadata_history_capability(self):
        status = self.client.get("/api/status")
        self.assertEqual(status.status_code, 200)
        pipeline = status.json()["data_pipeline"]
        self.assertTrue(pipeline["persistent_history"])
        self.assertEqual(pipeline["history_kind"], "portals_metadata_snapshots")
        self.assertEqual(pipeline["history_limit_per_gift"], 50)
        self.assertTrue(pipeline["history_requires_persistent_volume_for_deploys"])

    def test_portals_attributes_are_normalized_and_bounded(self):
        attributes = [{"type": "model", "value": "x" * 300}, "invalid", {"name": {"nested": "value"}}] + [
            {"type": "symbol", "value": str(index)} for index in range(50)
        ]
        normalized = api._safe_gift_attributes(attributes)
        self.assertEqual(len(normalized), 38)
        self.assertLessEqual(len(normalized), 40)
        self.assertEqual(normalized[0]["value"], "x" * 160)
        self.assertEqual(normalized[1]["name"], "")
        self.assertTrue(all(set(item) == {"type", "name", "value"} for item in normalized))

    def test_portals_catalogue_is_persisted_and_searchable_by_exact_item(self):
        payload = {
            "results": [{
                "id": "portals-item-9001",
                "name": "Plush Pepe",
                "external_collection_number": 9001,
                "collection_id": "plush-pepe",
                "photo_url": "https://example.invalid/gift.webp",
                "animation_url": None,
                "price": "4.2",
                "floor_price": "3.9",
                "attributes": [{"type": "model", "value": "Pepe"}],
                "status": "listed",
                "listed_at": "2026-11-11T00:00:00Z",
            }],
            "total_count": 1,
        }
        with patch.dict(os.environ, {"PORTALS_API_KEY": "test-partner-key"}), patch.object(
            api, "_portals_get_json", new_callable=AsyncMock, return_value=payload
        ):
            loaded = self.client.get("/api/gifts?limit=1")
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["items"][0]["id"], "portals-item-9001")

        exact = self.client.get("/api/catalog/items/portals-item-9001")
        self.assertEqual(exact.status_code, 200)
        self.assertTrue(exact.json()["metadata_only"])
        self.assertEqual(exact.json()["item"]["number"], 9001)
        self.assertEqual(exact.json()["item"]["catalogue_source"], "Portals")

        searched = self.client.get("/api/catalog/items?search=Pepe&collection_id=plush-pepe")
        self.assertEqual(searched.status_code, 200)
        self.assertEqual(searched.json()["total_count"], 1)
        self.assertEqual(searched.json()["items"][0]["id"], "portals-item-9001")

    def test_catalogue_history_endpoint_returns_distinct_metadata_versions(self):
        payloads = [
            {"results": [{"id": "history-item-1", "name": "Gift", "price": "1.0"}], "total_count": 1},
            {"results": [{"id": "history-item-1", "name": "Gift", "price": "1.5"}], "total_count": 1},
        ]
        with patch.dict(os.environ, {"PORTALS_API_KEY": "test-partner-key"}), patch.object(
            api, "_portals_get_json", new_callable=AsyncMock, side_effect=payloads
        ):
            first = self.client.get("/api/gifts?limit=1&sort_by=price%20asc")
            second = self.client.get("/api/gifts?limit=1&sort_by=price%20desc")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        history = self.client.get("/api/catalog/items/history-item-1/history")
        self.assertEqual(history.status_code, 200)
        self.assertTrue(history.json()["metadata_only"])
        self.assertEqual(len(history.json()["history"]), 2)
        self.assertEqual(
            {entry["item"]["price"] for entry in history.json()["history"]},
            {"1.0", "1.5"},
        )

    def test_catalogue_item_endpoint_does_not_invent_missing_items(self):
        missing = self.client.get("/api/catalog/items/not-indexed")
        self.assertEqual(missing.status_code, 404)

    def test_price_filter_rejects_minimum_above_maximum(self):
        with patch.dict(os.environ, {"PORTALS_API_KEY": "test-partner-key"}), patch.object(api, "_portals_get_json", new_callable=AsyncMock) as upstream:
            response = self.client.get("/api/gifts?min_price=5&max_price=2")
        self.assertEqual(response.status_code, 422)
        self.assertIn("min_price cannot exceed max_price", response.json()["detail"])
        upstream.assert_not_awaited()

    def test_favorites_require_telegram_auth_and_limit_payload_size(self):
        unauthenticated = self.client.get("/api/marketplace/favorites")
        self.assertEqual(unauthenticated.status_code, 401)
        oversized = self.client.put(
            "/api/marketplace/favorites/large",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"gift": {"id": "large", "description": "x" * 17000}},
        )
        self.assertEqual(oversized.status_code, 413)

    def test_orders_are_shared_and_stats_are_server_calculated(self):
        created = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"kind": "listing", "title": "Plush Pepe", "price": "2.5", "currency": "TON", "note": "demo"},
        )
        self.assertEqual(created.status_code, 201)
        order_id = created.json()["item"]["id"]

        mine = self.client.get(
            "/api/marketplace/mine",
            headers={"X-Telegram-Init-Data": "user-a"},
        )
        self.assertEqual(mine.status_code, 200)
        self.assertEqual([item["id"] for item in mine.json()["items"]], [order_id])

        public = self.client.get(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-b"},
        )
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json()["total"], 1)
        self.assertEqual(public.json()["items"][0]["id"], order_id)
        self.assertFalse(public.json()["items"][0]["isMine"])

        filled = self.client.post(
            f"/api/marketplace/orders/{order_id}/simulate-fill",
            headers={"X-Telegram-Init-Data": "user-b"},
        )
        self.assertEqual(filled.status_code, 200)
        self.assertTrue(filled.json()["simulated"])

        stats = self.client.get("/api/marketplace/stats").json()
        self.assertEqual(stats["active_listings"], 0)
        self.assertEqual(stats["matched_orders"], 1)
        self.assertEqual(stats["volume_ton"], 2.5)

    def test_owner_cannot_simulate_fill_own_order_and_can_cancel(self):
        created = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"kind": "buy-order", "title": "Cap", "price": 50, "currency": "XTR"},
        )
        order_id = created.json()["item"]["id"]
        own_fill = self.client.post(
            f"/api/marketplace/orders/{order_id}/simulate-fill",
            headers={"X-Telegram-Init-Data": "user-a"},
        )
        self.assertEqual(own_fill.status_code, 403)
        cancelled = self.client.delete(
            f"/api/marketplace/orders/{order_id}",
            headers={"X-Telegram-Init-Data": "user-a"},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["item"]["status"], "cancelled")

    def test_deal_history_pagination_is_private_to_participants(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        for index in range(3):
            order = self.client.post(
                "/api/marketplace/orders", headers=owner_headers,
                json={"kind": "listing", "title": f"Deal Page Gift {index}", "price": "1", "currency": "TON"},
            )
            self.assertEqual(order.status_code, 201)
            order_id = order.json()["item"]["id"]
            offered = self.client.post(
                "/api/marketplace/offers", headers=sender_headers,
                json={"order_id": order_id, "amount": "1"},
            )
            self.assertEqual(offered.status_code, 201)
            offer_id = offered.json()["item"]["id"]
            accepted = self.client.patch(
                f"/api/marketplace/offers/{offer_id}", headers=owner_headers,
                json={"action": "accept"},
            )
            self.assertEqual(accepted.status_code, 200)

        first_page = self.client.get("/api/marketplace/deals?limit=2&offset=0", headers=sender_headers)
        second_page = self.client.get("/api/marketplace/deals?limit=2&offset=2", headers=sender_headers)
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.json()["total"], 3)
        self.assertEqual(len(first_page.json()["items"]), 2)
        self.assertEqual(len(second_page.json()["items"]), 1)
        unrelated = self.client.get(
            "/api/marketplace/deals?limit=10",
            headers={"X-Telegram-Init-Data": "user-c"},
        )
        self.assertEqual(unrelated.json()["total"], 0)
        self.assertEqual(unrelated.json()["items"], [])

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

    def test_offer_acceptance_creates_simulated_deal_and_closes_order(self):
        created = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"kind": "listing", "title": "Plush Pepe", "price": "2.5", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        offered = self.client.post(
            "/api/marketplace/offers",
            headers={"X-Telegram-Init-Data": "user-b"},
            json={"order_id": order_id, "amount": "2.2", "note": "demo offer"},
        )
        self.assertEqual(offered.status_code, 201)
        offer_id = offered.json()["item"]["id"]
        self.assertEqual(offered.json()["item"]["status"], "pending")
        self.assertTrue(offered.json()["simulation_only"])

        received = self.client.get("/api/marketplace/offers", headers={"X-Telegram-Init-Data": "user-a"})
        self.assertEqual(len(received.json()["items"]), 1)
        self.assertFalse(received.json()["items"][0]["is_mine"])

        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer_id}",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["item"]["status"], "accepted")
        self.assertTrue(accepted.json()["deal_created"])

        deals = self.client.get("/api/marketplace/deals", headers={"X-Telegram-Init-Data": "user-b"})
        self.assertEqual(len(deals.json()["items"]), 1)
        self.assertEqual(deals.json()["items"][0]["amount"], "2.2")
        self.assertTrue(deals.json()["items"][0]["simulation_only"])

        mine = self.client.get("/api/marketplace/mine", headers={"X-Telegram-Init-Data": "user-a"})
        self.assertEqual(mine.json()["items"][0]["status"], "matched")

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
            json={"title": "Updated Gift", "price": "3.75", "note": "new note"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["item"]["title"], "Updated Gift")
        self.assertEqual(updated.json()["item"]["price"], "3.75")
        self.assertEqual(updated.json()["item"]["note"], "new note")
        self.assertGreaterEqual(updated.json()["item"]["updatedAt"], created.json()["item"]["updatedAt"])

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
            json={"title": "After edit", "price": "2.5"},
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
                "SELECT status FROM demo_order_reports WHERE id = ?", (report_id,),
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
                    """INSERT INTO demo_orders
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
                "Origin": "https://nft-gift-scanner.fj7kmzkhn9.workers.dev",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "content-type,x-telegram-init-data",
            },
        )
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "https://nft-gift-scanner.fj7kmzkhn9.workers.dev",
        )
        self.assertIn("PATCH", response.headers.get("access-control-allow-methods", ""))

    def test_cors_allows_production_mini_app_origin(self):
        response = self.client.options(
            "/api/marketplace/orders",
            headers={
                "Origin": "https://nft-gift-scanner.fj7kmzkhn9.workers.dev",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type,x-telegram-init-data",
            },
        )
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "https://nft-gift-scanner.fj7kmzkhn9.workers.dev",
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
            json={"kind": "listing", "title": "Gift Radar test", "price": "3", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        offered = self.client.post(
            "/api/marketplace/offers", headers=headers_b,
            json={"order_id": order_id, "amount": "2.5"},
        )
        self.assertEqual(offered.status_code, 201)

        owner_feed = self.client.get("/api/marketplace/notifications", headers=headers_a)
        self.assertEqual(owner_feed.status_code, 200)
        self.assertEqual(owner_feed.json()["unread_count"], 1)
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

        offer_id = offered.json()["item"]["id"]
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer_id}", headers=headers_a,
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200)
        sender_feed = self.client.get("/api/marketplace/notifications", headers=headers_b)
        self.assertEqual(sender_feed.json()["items"][0]["type"], "offer_accepted")
        stats = self.client.get("/api/marketplace/account/stats", headers=headers_b)
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.json()["simulated_deals"], 1)
        self.assertEqual(stats.json()["unread_notifications"], 1)
        self.assertTrue(stats.json()["simulation_only"])

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
        offer_id = offered.json()["item"]["id"]

        owner_activity = self.client.get("/api/marketplace/activity?limit=1", headers=headers_a)
        self.assertEqual(owner_activity.status_code, 200)
        self.assertEqual(owner_activity.json()["total"], 2)
        self.assertEqual(len(owner_activity.json()["items"]), 1)
        self.assertEqual(owner_activity.json()["items"][0]["type"], "offer_received")
        older = self.client.get("/api/marketplace/activity?limit=5&offset=1", headers=headers_a)
        self.assertEqual(older.json()["items"][0]["type"], "order_created")

        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer_id}", headers=headers_a,
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200)

        sender_activity = self.client.get("/api/marketplace/activity", headers=headers_b)
        sender_types = {item["type"] for item in sender_activity.json()["items"]}
        self.assertIn("offer_accepted", sender_types)
        self.assertIn("demo_deal_created", sender_types)
        self.assertTrue(all(item["simulation_only"] for item in sender_activity.json()["items"]))

        unrelated = self.client.get("/api/marketplace/activity", headers=headers_c)
        self.assertEqual(unrelated.json()["items"], [])
        self.assertEqual(unrelated.json()["total"], 0)

    def test_account_activity_requires_verified_telegram_session(self):
        response = self.client.get("/api/marketplace/activity")
        self.assertEqual(response.status_code, 401)

    def test_account_stats_require_verified_telegram_session(self):
        response = self.client.get("/api/marketplace/account/stats")
        self.assertEqual(response.status_code, 401)



    def test_deal_events_are_immutable_and_visible_only_to_participants(self):
        created = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"kind": "listing", "title": "Rare Gift", "price": "5", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        offered = self.client.post(
            "/api/marketplace/offers",
            headers={"X-Telegram-Init-Data": "user-b"},
            json={"order_id": order_id, "amount": "4.5"},
        )
        offer_id = offered.json()["item"]["id"]
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer_id}",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200)

        deals = self.client.get("/api/marketplace/deals", headers={"X-Telegram-Init-Data": "user-b"})
        deal_id = deals.json()["items"][0]["id"]
        events = self.client.get(
            f"/api/marketplace/deals/{deal_id}/events",
            headers={"X-Telegram-Init-Data": "user-b"},
        )
        self.assertEqual(events.status_code, 200)
        self.assertEqual(
            [item["type"] for item in events.json()["items"]],
            ["deal_created", "listing_reserved", "payment_not_enabled"],
        )
        self.assertTrue(all(item["simulation_only"] for item in events.json()["items"]))
        self.assertEqual(events.json()["items"][-1]["data"]["reason"], "payment_integration_not_live")

        outsider = self.client.get(
            f"/api/marketplace/deals/{deal_id}/events",
            headers={"X-Telegram-Init-Data": "user-c"},
        )
        self.assertEqual(outsider.status_code, 404)

        with demo_offers._db() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE demo_deal_events SET event_data='{}' WHERE deal_id=?",
                    (deal_id,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "DELETE FROM demo_deal_events WHERE deal_id=?",
                    (deal_id,),
                )

    def test_accepting_one_offer_atomically_reserves_listing_and_declines_competing_offers(self):
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

        first = self.client.patch(
            f"/api/marketplace/offers/{offer_ids[0]}",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"action": "accept"},
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.patch(
            f"/api/marketplace/offers/{offer_ids[1]}",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"action": "accept"},
        )
        self.assertEqual(second.status_code, 409)

        order = self.client.get(
            f"/api/marketplace/orders?status=matched",
        )
        self.assertTrue(any(item["id"] == order_id for item in order.json()["items"]))
        for user_token, expected in (("user-b", "accepted"), ("user-c", "declined")):
            offers = self.client.get(
                "/api/marketplace/offers",
                headers={"X-Telegram-Init-Data": user_token},
            )
            own = next(item for item in offers.json()["items"] if item["order_id"] == order_id)
            self.assertEqual(own["status"], expected)

    def test_buy_request_deal_reports_buyer_and_seller_roles_correctly(self):
        created = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"kind": "buy-order", "title": "Looking for Rare Gift", "price": "9", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        offered = self.client.post(
            "/api/marketplace/offers",
            headers={"X-Telegram-Init-Data": "user-b"},
            json={"order_id": order_id, "amount": "8.5"},
        )
        self.assertEqual(offered.status_code, 201)
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offered.json()['item']['id']}",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200)

        buyer_view = self.client.get("/api/marketplace/deals", headers={"X-Telegram-Init-Data": "user-a"})
        seller_view = self.client.get("/api/marketplace/deals", headers={"X-Telegram-Init-Data": "user-b"})
        buyer_deal = buyer_view.json()["items"][0]
        seller_deal = seller_view.json()["items"][0]
        self.assertEqual(buyer_deal["buyer"], "Tester A")
        self.assertEqual(buyer_deal["seller"], "Tester B")
        self.assertEqual(buyer_deal["my_role"], "buyer")
        self.assertEqual(seller_deal["my_role"], "seller")
        self.assertEqual(buyer_deal["order_kind"], "buy-order")
        self.assertTrue(buyer_deal["simulation_only"])


class PortalsReadOnlyClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.token_patch = patch.dict(os.environ, {"PORTALS_API_KEY": "test-partner-key"})
        self.token_patch.start()
        self.lock_patch = patch.object(api, "_request_lock", asyncio.Lock())
        self.lock_patch.start()
        self.time_patch = patch.object(api, "_last_request_at", 0.0)
        self.time_patch.start()

    async def asyncTearDown(self):
        self.time_patch.stop()
        self.lock_patch.stop()
        self.token_patch.stop()

    async def test_portals_search_retries_rate_limit_and_keeps_auth_server_side(self):
        class Response:
            def __init__(self, status_code, payload, headers=None):
                self.status_code = status_code
                self._payload = payload
                self.headers = headers or {}
                self.is_error = status_code >= 400

            def json(self):
                return self._payload

        class Client:
            def __init__(self):
                self.calls = []
                self.responses = [
                    Response(429, {}, {"Retry-After": "0"}),
                    Response(200, {"results": []}),
                ]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, headers, params):
                self.calls.append((url, headers, params))
                return self.responses.pop(0)

        client = Client()
        with patch.object(api.httpx, "AsyncClient", return_value=client), patch.object(api.asyncio, "sleep", new=AsyncMock()):
            result = await api._portals_get_json("/nfts/search", {"limit": 10})

        self.assertEqual(result, {"results": []})
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0][1]["Authorization"], "partners test-partner-key")
        self.assertEqual(client.calls[0][2], {"limit": 10})

    async def test_portals_client_rejects_non_allowlisted_routes(self):
        with self.assertRaises(api.HTTPException) as raised:
            await api._portals_get_json("/partners/market/buy", {})
        self.assertEqual(raised.exception.status_code, 400)



class TelegramInitDataValidationTests(unittest.TestCase):
    def _signed_init_data(self, auth_date: int | None = None) -> str:
        bot_token = "test-bot-token"
        values = {
            "auth_date": str(auth_date if auth_date is not None else int(time.time())),
            "query_id": "AAE-test-query",
            "user": json.dumps({"id": 123456, "first_name": "Test"}, separators=(",", ":")),
        }
        check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        values["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        return urlencode(values)

    def test_raw_init_data_cannot_be_used_directly_as_an_api_session(self):
        with self.assertRaises(api.HTTPException) as raised:
            api._authenticate_telegram_credential("auth_date=123&hash=" + "a" * 64)
        self.assertEqual(raised.exception.status_code, 401)

    def test_valid_telegram_init_data_is_verified(self):
        with patch.dict(os.environ, {"BOT_TOKEN": "test-bot-token", "TELEGRAM_INITDATA_MAX_AGE_SECONDS": "300"}):
            user = api._verify_telegram_init_data(self._signed_init_data())
        self.assertEqual(user["id"], 123456)

    def test_tampered_or_duplicate_init_data_is_rejected(self):
        signed = self._signed_init_data()
        tampered = signed.replace("first_name", "last_name")
        with patch.dict(os.environ, {"BOT_TOKEN": "test-bot-token"}):
            with self.assertRaises(api.HTTPException) as invalid_signature:
                api._verify_telegram_init_data(tampered)
            self.assertEqual(invalid_signature.exception.status_code, 401)
            with self.assertRaises(api.HTTPException) as duplicate:
                api._verify_telegram_init_data("auth_date=1&auth_date=2&hash=" + "0" * 64)
            self.assertEqual(duplicate.exception.status_code, 401)

    def test_expired_and_future_init_data_is_rejected(self):
        now = int(time.time())
        with patch.dict(os.environ, {"BOT_TOKEN": "test-bot-token", "TELEGRAM_INITDATA_MAX_AGE_SECONDS": "60"}):
            with self.assertRaises(api.HTTPException):
                api._verify_telegram_init_data(self._signed_init_data(now - 120))
            with self.assertRaises(api.HTTPException):
                api._verify_telegram_init_data(self._signed_init_data(now + 120))


class TelegramSessionSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(marketplace, "DB_PATH", os.path.join(self.temp_dir.name, "sessions.sqlite3"))
        self.db_patch.start()
        marketplace.init_marketplace_db()
        self.auth_patch = patch.object(
            api,
            "_verify_telegram_init_data",
            side_effect=lambda value: {
                "id": "101",
                "first_name": "Session Tester",
                "username": "session_tester",
            },
        )
        self.auth_patch.start()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        self.auth_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_session_list_and_single_session_logout(self):
        login = self.client.post("/api/auth/telegram", json={"init_data": "one-time-init-data"})
        self.assertEqual(login.status_code, 200)
        token = login.json()["session_token"]
        headers = {"X-Telegram-Init-Data": token}
        sessions = self.client.get("/api/auth/sessions", headers=headers)
        self.assertEqual(sessions.status_code, 200)
        self.assertEqual(len(sessions.json()["items"]), 1)
        self.assertTrue(sessions.json()["items"][0]["current"])
        logout = self.client.post("/api/auth/logout", headers=headers)
        self.assertEqual(logout.status_code, 200)
        self.assertTrue(logout.json()["revoked"])
        self.assertIsNone(marketplace.get_telegram_session_user(token))
        denied = self.client.get("/api/auth/sessions", headers=headers)
        self.assertEqual(denied.status_code, 401)

    def test_logout_all_revokes_every_account_session(self):
        marketplace.upsert_telegram_user({"id": "101", "first_name": "Tester"})
        first = marketplace.create_telegram_session("101")
        second = marketplace.create_telegram_session("101")
        response = self.client.post(
            "/api/auth/logout-all",
            headers={"X-Telegram-Init-Data": first},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["revoked_sessions"], 2)
        self.assertIsNone(marketplace.get_telegram_session_user(first))
        self.assertIsNone(marketplace.get_telegram_session_user(second))

    def test_telegram_webhook_inbox_is_idempotent_and_detects_payload_conflicts(self):
        payload = {"update_id": 777, "message": {"text": "/start"}}
        self.assertEqual(marketplace.claim_telegram_update(777, payload), "claimed")
        self.assertEqual(marketplace.claim_telegram_update(777, payload), "in_progress")
        marketplace.finish_telegram_update(777)
        self.assertEqual(marketplace.claim_telegram_update(777, payload), "processed")
        self.assertEqual(
            marketplace.claim_telegram_update(777, {"update_id": 777, "message": {"text": "/help"}}),
            "conflict",
        )

    def test_failed_telegram_webhook_update_can_be_retried(self):
        payload = {"update_id": 778, "message": {"text": "/start"}}
        self.assertEqual(marketplace.claim_telegram_update(778, payload), "claimed")
        marketplace.release_telegram_update(778)
        self.assertEqual(marketplace.claim_telegram_update(778, payload), "claimed")





class PortalsReadOnlyClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.token_patch = patch.dict(os.environ, {"PORTALS_API_KEY": "test-partner-key"})
        self.token_patch.start()
        self.lock_patch = patch.object(api, "_request_lock", asyncio.Lock())
        self.lock_patch.start()
        self.time_patch = patch.object(api, "_last_request_at", 0.0)
        self.time_patch.start()

    async def asyncTearDown(self):
        self.time_patch.stop()
        self.lock_patch.stop()
        self.token_patch.stop()

    async def test_portals_search_retries_rate_limit_and_keeps_auth_server_side(self):
        class Response:
            def __init__(self, status_code, payload, headers=None):
                self.status_code = status_code
                self._payload = payload
                self.headers = headers or {}
                self.is_error = status_code >= 400

            def json(self):
                return self._payload

        class Client:
            def __init__(self):
                self.calls = []
                self.responses = [
                    Response(429, {}, {"Retry-After": "0"}),
                    Response(200, {"results": []}),
                ]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, headers, params):
                self.calls.append((url, headers, params))
                return self.responses.pop(0)

        client = Client()
        with patch.object(api.httpx, "AsyncClient", return_value=client), patch.object(api.asyncio, "sleep", new=AsyncMock()):
            result = await api._portals_get_json("/nfts/search", {"limit": 10})

        self.assertEqual(result, {"results": []})
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0][1]["Authorization"], "partners test-partner-key")
        self.assertEqual(client.calls[0][2], {"limit": 10})

    async def test_portals_client_rejects_non_allowlisted_routes(self):
        with self.assertRaises(api.HTTPException) as raised:
            await api._portals_get_json("/partners/market/buy", {})
        self.assertEqual(raised.exception.status_code, 400)



class TelegramInitDataValidationTests(unittest.TestCase):
    def _signed_init_data(self, auth_date: int | None = None) -> str:
        bot_token = "test-bot-token"
        values = {
            "auth_date": str(auth_date if auth_date is not None else int(time.time())),
            "query_id": "AAE-test-query",
            "user": json.dumps({"id": 123456, "first_name": "Test"}, separators=(",", ":")),
        }
        check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        values["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        return urlencode(values)

    def test_raw_init_data_cannot_be_used_directly_as_an_api_session(self):
        with self.assertRaises(api.HTTPException) as raised:
            api._authenticate_telegram_credential("auth_date=123&hash=" + "a" * 64)
        self.assertEqual(raised.exception.status_code, 401)

    def test_valid_telegram_init_data_is_verified(self):
        with patch.dict(os.environ, {"BOT_TOKEN": "test-bot-token", "TELEGRAM_INITDATA_MAX_AGE_SECONDS": "300"}):
            user = api._verify_telegram_init_data(self._signed_init_data())
        self.assertEqual(user["id"], 123456)

    def test_tampered_or_duplicate_init_data_is_rejected(self):
        signed = self._signed_init_data()
        tampered = signed.replace("first_name", "last_name")
        with patch.dict(os.environ, {"BOT_TOKEN": "test-bot-token"}):
            with self.assertRaises(api.HTTPException) as invalid_signature:
                api._verify_telegram_init_data(tampered)
            self.assertEqual(invalid_signature.exception.status_code, 401)
            with self.assertRaises(api.HTTPException) as duplicate:
                api._verify_telegram_init_data("auth_date=1&auth_date=2&hash=" + "0" * 64)
            self.assertEqual(duplicate.exception.status_code, 401)

    def test_expired_and_future_init_data_is_rejected(self):
        now = int(time.time())
        with patch.dict(os.environ, {"BOT_TOKEN": "test-bot-token", "TELEGRAM_INITDATA_MAX_AGE_SECONDS": "60"}):
            with self.assertRaises(api.HTTPException):
                api._verify_telegram_init_data(self._signed_init_data(now - 120))
            with self.assertRaises(api.HTTPException):
                api._verify_telegram_init_data(self._signed_init_data(now + 120))


class TelegramSessionSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(marketplace, "DB_PATH", os.path.join(self.temp_dir.name, "sessions.sqlite3"))
        self.db_patch.start()
        marketplace.init_marketplace_db()
        self.auth_patch = patch.object(
            api,
            "_verify_telegram_init_data",
            side_effect=lambda value: {
                "id": "101",
                "first_name": "Session Tester",
                "username": "session_tester",
            },
        )
        self.auth_patch.start()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        self.auth_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_session_list_and_single_session_logout(self):
        login = self.client.post("/api/auth/telegram", json={"init_data": "one-time-init-data"})
        self.assertEqual(login.status_code, 200)
        token = login.json()["session_token"]
        headers = {"X-Telegram-Init-Data": token}
        sessions = self.client.get("/api/auth/sessions", headers=headers)
        self.assertEqual(sessions.status_code, 200)
        self.assertEqual(len(sessions.json()["items"]), 1)
        self.assertTrue(sessions.json()["items"][0]["current"])
        logout = self.client.post("/api/auth/logout", headers=headers)
        self.assertEqual(logout.status_code, 200)
        self.assertTrue(logout.json()["revoked"])
        self.assertIsNone(marketplace.get_telegram_session_user(token))
        denied = self.client.get("/api/auth/sessions", headers=headers)
        self.assertEqual(denied.status_code, 401)

    def test_logout_all_revokes_every_account_session(self):
        marketplace.upsert_telegram_user({"id": "101", "first_name": "Tester"})
        first = marketplace.create_telegram_session("101")
        second = marketplace.create_telegram_session("101")
        response = self.client.post(
            "/api/auth/logout-all",
            headers={"X-Telegram-Init-Data": first},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["revoked_sessions"], 2)
        self.assertIsNone(marketplace.get_telegram_session_user(first))
        self.assertIsNone(marketplace.get_telegram_session_user(second))

    def test_telegram_webhook_inbox_is_idempotent_and_detects_payload_conflicts(self):
        payload = {"update_id": 777, "message": {"text": "/start"}}
        self.assertEqual(marketplace.claim_telegram_update(777, payload), "claimed")
        self.assertEqual(marketplace.claim_telegram_update(777, payload), "in_progress")
        marketplace.finish_telegram_update(777)
        self.assertEqual(marketplace.claim_telegram_update(777, payload), "processed")
        self.assertEqual(
            marketplace.claim_telegram_update(777, {"update_id": 777, "message": {"text": "/help"}}),
            "conflict",
        )

    def test_failed_telegram_webhook_update_can_be_retried(self):
        payload = {"update_id": 778, "message": {"text": "/start"}}
        self.assertEqual(marketplace.claim_telegram_update(778, payload), "claimed")
        marketplace.release_telegram_update(778)
        self.assertEqual(marketplace.claim_telegram_update(778, payload), "claimed")



class SellerProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(marketplace, "DB_PATH", os.path.join(self.temp_dir.name, "profiles.sqlite3"))
        self.db_patch.start()
        marketplace.init_marketplace_db()
        marketplace.upsert_telegram_user({"id": "101", "first_name": "Profile Tester", "username": "profile_tester"})
        self.auth_patch = patch.object(
            api,
            "_authenticate_telegram_credential",
            side_effect=lambda token: (
                {"id": "101", "first_name": "Profile Tester", "username": "profile_tester"}
                if token == "profile-session" else None
            ),
        )
        self.auth_patch.start()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        self.auth_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_seller_profile_can_be_created_updated_and_viewed_publicly(self):
        headers = {"X-Telegram-Init-Data": "profile-session"}
        initial = self.client.get("/api/marketplace/profiles/me", headers=headers)
        self.assertEqual(initial.status_code, 200)
        profile = initial.json()["profile"]
        self.assertEqual(profile["display_name"], "Profile Tester")
        self.assertTrue(profile["profile_id"].startswith("usr_"))

        updated = self.client.put(
            "/api/marketplace/profiles/me",
            headers=headers,
            json={"display_name": "Gift Seller", "bio": "Collecting rare Telegram gifts."},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["profile"]["display_name"], "Gift Seller")
        self.assertEqual(updated.json()["profile"]["bio"], "Collecting rare Telegram gifts.")

        public = self.client.get(f"/api/marketplace/profiles/{profile['profile_id']}")
        self.assertEqual(public.status_code, 200)
        public_profile = public.json()["profile"]
        self.assertEqual(public_profile["display_name"], "Gift Seller")
        self.assertEqual(public_profile["username"], "profile_tester")
        self.assertNotIn("telegram_id", public_profile)
        self.assertNotIn("session_token", public_profile)
        self.assertEqual(public_profile["completed_trades"], 0)
        self.assertIsNone(public_profile["rating"])

        now = int(time.time())
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO demo_orders
                   (id, user_id, owner_name, kind, title, price, currency, status, created_at, updated_at, note)
                   VALUES (?, ?, ?, 'listing', ?, ?, 'TON', 'active', ?, ?, ?)""",
                ("seller-listing-1", "101", "Profile Tester", "Rare Gift #1", "2.5", now, now, "Public note"),
            )
        orders = self.client.get("/api/marketplace/orders")
        self.assertEqual(orders.status_code, 200)
        order = next(item for item in orders.json()["items"] if item["id"] == "seller-listing-1")
        self.assertEqual(order["seller_profile_id"], profile["profile_id"])
        self.assertNotIn("user_id", order)

        public_with_listing = self.client.get(f"/api/marketplace/profiles/{profile['profile_id']}")
        seller_listing = public_with_listing.json()["profile"]["listings"][0]
        self.assertEqual(seller_listing["id"], "seller-listing-1")
        self.assertEqual(seller_listing["title"], "Rare Gift #1")
        self.assertNotIn("user_id", seller_listing)
        self.assertNotIn("telegram_id", seller_listing)

    def test_profile_update_rejects_blank_name_and_oversized_bio(self):
        headers = {"X-Telegram-Init-Data": "profile-session"}
        blank = self.client.put(
            "/api/marketplace/profiles/me",
            headers=headers,
            json={"display_name": "   ", "bio": ""},
        )
        self.assertEqual(blank.status_code, 422)
        oversized = self.client.put(
            "/api/marketplace/profiles/me",
            headers=headers,
            json={"display_name": "Seller", "bio": "x" * 281},
        )
        self.assertEqual(oversized.status_code, 422)

    def test_profile_endpoints_require_auth_for_private_profile(self):
        response = self.client.get("/api/marketplace/profiles/me")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
