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

    def test_database_connections_enforce_durability_and_lock_wait_policy(self):
        with marketplace._db() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        with demo_offers._db() as conn:
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

Z",
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
        deal_payload = next(item for item in deals.json()["items"] if item["id"] == deal_id)
        self.assertEqual(deal_payload["status"], "payment_not_enabled")
        self.assertTrue(deal_payload["simulation_only"])
        self.assertFalse(deals.json()["payments_enabled"])
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


    def test_participant_can_cancel_unpaid_deal_and_release_listing_atomically(self):
        owner = {"X-Telegram-Init-Data": "user-a"}
        buyer = {"X-Telegram-Init-Data": "user-b"}
        outsider = {"X-Telegram-Init-Data": "user-c"}
        order = self.client.post(
            "/api/marketplace/orders", headers=owner,
            json={"kind": "listing", "title": "Releaseable Gift", "price": "3", "currency": "TON"},
        )
        order_id = order.json()["item"]["id"]
        offer = self.client.post(
            "/api/marketplace/offers", headers=buyer,
            json={"order_id": order_id, "amount": "2.8"},
        )
        offer_id = offer.json()["item"]["id"]
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer_id}", headers=owner,
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200)
        deal_id = self.client.get("/api/marketplace/deals", headers=buyer).json()["items"][0]["id"]

        forbidden = self.client.post(
            f"/api/marketplace/deals/{deal_id}/actions", headers=outsider,
            json={"action": "cancel"},
        )
        self.assertEqual(forbidden.status_code, 404)

        cancelled = self.client.post(
            f"/api/marketplace/deals/{deal_id}/actions", headers=buyer,
            json={"action": "cancel"},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertTrue(cancelled.json()["reservation_released"])
        self.assertFalse(cancelled.json()["payments_enabled"])

        active = self.client.get("/api/marketplace/orders?status=active")
        self.assertTrue(any(item["id"] == order_id for item in active.json()["items"]))
        deals = self.client.get("/api/marketplace/deals", headers=owner)
        deal = next(item for item in deals.json()["items"] if item["id"] == deal_id)
        self.assertEqual(deal["status"], "cancelled")

        history = self.client.get(
            f"/api/marketplace/deals/{deal_id}/events", headers=owner,
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(
            [item["type"] for item in history.json()["items"]],
            ["deal_created", "listing_reserved", "payment_not_enabled", "deal_cancelled"],
        )
        self.assertTrue(history.json()["items"][-1]["data"]["reservation_released"])

        repeated = self.client.post(
            f"/api/marketplace/deals/{deal_id}/actions", headers=owner,
            json={"action": "cancel"},
        )
        self.assertEqual(repeated.status_code, 200)
        self.assertTrue(repeated.json()["already_cancelled"])
        history_again = self.client.get(
            f"/api/marketplace/deals/{deal_id}/events", headers=owner,
        )
        self.assertEqual(len(history_again.json()["items"]), 4)

        # A cancelled reservation must not permanently consume the order's unique deal slot.
        second_offer = self.client.post(
            "/api/marketplace/offers", headers=outsider,
            json={"order_id": order_id, "amount": "2.7"},
        )
        self.assertEqual(second_offer.status_code, 201)
        second_accept = self.client.patch(
            f"/api/marketplace/offers/{second_offer.json()['item']['id']}",
            headers=owner, json={"action": "accept"},
        )
        self.assertEqual(second_accept.status_code, 200)
        all_deals = self.client.get("/api/marketplace/deals", headers=owner).json()["items"]
        self.assertEqual(len([item for item in all_deals if item["order_id"] == order_id]), 2)
        self.assertEqual(
            next(item for item in all_deals if item["id"] == deal_id)["status"],
            "cancelled",
        )

    def test_cannot_cancel_deal_when_payment_state_is_not_unpaid(self):
        owner = {"X-Telegram-Init-Data": "user-a"}
        buyer = {"X-Telegram-Init-Data": "user-b"}
        order = self.client.post(
            "/api/marketplace/orders", headers=owner,
            json={"kind": "listing", "title": "Protected Gift", "price": "4", "currency": "TON"},
        )
        offer = self.client.post(
            "/api/marketplace/offers", headers=buyer,
            json={"order_id": order.json()["item"]["id"], "amount": "3.5"},
        )
        self.client.patch(
            f"/api/marketplace/offers/{offer.json()['item']['id']}", headers=owner,
            json={"action": "accept"},
        )
        deal_id = self.client.get("/api/marketplace/deals", headers=buyer).json()["items"][0]["id"]
        with demo_offers._db() as conn:
            conn.execute("UPDATE demo_deals SET status='paid' WHERE id=?", (deal_id,))
        response = self.client.post(
            f"/api/marketplace/deals/{deal_id}/actions", headers=buyer,
            json={"action": "cancel"},
        )
        self.assertEqual(response.status_code, 409)


