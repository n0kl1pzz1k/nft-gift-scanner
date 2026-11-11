import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import asyncio

from fastapi.testclient import TestClient

import app as api
import marketplace


class SharedMarketplaceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(marketplace, "DB_PATH", os.path.join(self.temp_dir.name, "test.sqlite3"))
        self.db_patch.start()
        marketplace.init_marketplace_db()
        self.auth_patch = patch.object(
            api,
            "_verify_telegram_init_data",
            side_effect=lambda value: {
                "id": "101" if value == "user-a" else "202",
                "first_name": "Tester A" if value == "user-a" else "Tester B",
                "username": "tester",
            },
        )
        self.auth_patch.start()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        self.auth_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_telegram_auth_persists_and_updates_user_profile(self):
        first = self.client.post("/api/auth/telegram", json={"init_data": "user-a"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["user"]["id"], "101")
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
            json={"gift": {"description": "x" * 17000}},
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

    def test_cors_rejects_untrusted_origins(self):
        response = self.client.options(
            "/api/marketplace/orders",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertNotIn("access-control-allow-origin", response.headers)

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


if __name__ == "__main__":
    unittest.main()
