import unittest
from unittest.mock import patch

from app import app


class ApiContractTests(unittest.TestCase):
    def test_scanner_catalogue_routes_are_removed(self):
        paths = set(app.openapi()['paths'])
        self.assertIn("/health", paths)
        self.assertIn("/api/auth/telegram", paths)
        self.assertIn("/api/marketplace/orders", paths)
        self.assertIn("/api/marketplace/stats", paths)
        self.assertIn("/api/telegram/webhook", paths)
        self.assertNotIn("/api/status", paths)
        self.assertNotIn("/api/collections", paths)
        self.assertNotIn("/api/gifts", paths)
        self.assertNotIn("/api/catalog/items", paths)
        self.assertNotIn("/api/reference/gift-changes", paths)
        self.assertNotIn("/api/reference/daily-floors", paths)

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
