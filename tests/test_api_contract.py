import unittest
from unittest.mock import patch

from app import app, _safe_total_count, _marketplace_visibility


class ApiContractTests(unittest.TestCase):
    def test_total_count_never_underreports_returned_items(self):
        self.assertEqual(_safe_total_count(0, 3), 3)
        self.assertEqual(_safe_total_count("12", 3), 12)
        self.assertEqual(_safe_total_count("invalid", 3), 3)

    def test_portals_is_the_only_marketplace_adapter(self):
        paths = set(app.openapi()['paths'])
        self.assertIn("/health", paths)
        self.assertIn("/api/status", paths)
        self.assertIn("/api/collections", paths)
        self.assertIn("/api/gifts", paths)
        self.assertIn("/api/auth/telegram", paths)
        self.assertIn("/api/marketplace/orders", paths)
        self.assertIn("/api/marketplace/stats", paths)
        self.assertIn("/api/telegram/webhook", paths)
        self.assertNotIn("/api/tonnel/gifts", paths)
        self.assertNotIn("/api/mrkt/gifts", paths)

    def test_security_response_headers_middleware_is_registered(self):
        middleware_names = [
            getattr(middleware.cls, "__name__", "")
            for middleware in app.user_middleware
        ]
        self.assertIn("BaseHTTPMiddleware", middleware_names)
        self.assertTrue(any(
            getattr(middleware, "options", {}).get("dispatch") is not None
            for middleware in app.user_middleware
        ))

    def test_marketplace_visibility_contains_only_portals(self):
        with patch("app.get_app_setting", return_value="true"):
            self.assertEqual(_marketplace_visibility(), {"portals": True})

    def test_reference_feeds_remain_separate_from_marketplace(self):
        paths = set(app.openapi()['paths'])
        self.assertIn("/api/reference/gift-changes", paths)
        self.assertIn("/api/reference/daily-floors", paths)

    def test_cors_allows_telegram_post_auth(self):
        cors = next(
            middleware for middleware in app.user_middleware
            if middleware.cls.__name__ == "CORSMiddleware"
        )
        self.assertIn("GET", cors.kwargs["allow_methods"])
        self.assertIn("POST", cors.kwargs["allow_methods"])
        self.assertIn("X-Telegram-Init-Data", cors.kwargs["allow_headers"])


if __name__ == "__main__":
    unittest.main()
