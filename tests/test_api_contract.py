import unittest

from app import app, _safe_total_count, _normalize_swift_item


class ApiContractTests(unittest.TestCase):
    def test_total_count_never_underreports_returned_items(self):
        self.assertEqual(_safe_total_count(0, 3), 3)
        self.assertEqual(_safe_total_count("12", 3), 12)
        self.assertEqual(_safe_total_count("invalid", 3), 3)

    def test_public_read_only_routes_exist(self):
        paths = {route.path for route in app.routes}
        self.assertIn("/health", paths)
        self.assertIn("/api/status", paths)
        self.assertIn("/api/collections", paths)
        self.assertIn("/api/gifts", paths)
        self.assertIn("/api/tonnel/gifts", paths)
        self.assertIn("/api/mrkt/gifts", paths)
        self.assertIn("/api/auth/telegram", paths)

    def test_swift_normalizer_discards_transaction_payload(self):
        item = _normalize_swift_item({
            "provider": "mrkt",
            "giftId": "gift-1",
            "title": "Test Gift",
            "price": 12.5,
            "photo_url": "https://example.com/gift.jpg",
            "attributes": {"model": {"value": "Gold", "rarity": "2%"}},
            "options": {"transaction": {"messages": [{"amount": "1"}]}},
        }, "mrkt")
        self.assertEqual(item["source"], "MRKT")
        self.assertEqual(item["currency"], "TON")
        self.assertEqual(item["attributes"][0]["value"], "Gold")
        self.assertNotIn("options", item)
        self.assertNotIn("transaction", item)

    def test_cors_allows_telegram_post_auth(self):
        cors = next(
            middleware for middleware in app.user_middleware
            if middleware.cls.__name__ == "CORSMiddleware"
        )
        self.assertIn("GET", cors.kwargs["allow_methods"])
        self.assertIn("POST", cors.kwargs["allow_methods"])
        self.assertIn("X-Telegram-Init-Data", cors.kwargs["allow_headers"])

    def test_marketplace_visibility_defaults_to_safe_read_only_scope(self):
        from app import _marketplace_visibility
        self.assertTrue(_marketplace_visibility["portals"])
        self.assertTrue(_marketplace_visibility["tonnel"])
        self.assertTrue(_marketplace_visibility["mrkt"])


if __name__ == "__main__":
    unittest.main()
