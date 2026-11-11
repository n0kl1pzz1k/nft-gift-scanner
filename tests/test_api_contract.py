import unittest

from app import app


class ApiContractTests(unittest.TestCase):
    def test_public_read_only_routes_exist(self):
        paths = {route.path for route in app.routes}
        self.assertIn("/health", paths)
        self.assertIn("/api/status", paths)
        self.assertIn("/api/collections", paths)
        self.assertIn("/api/gifts", paths)
        self.assertIn("/api/tonnel/gifts", paths)
        self.assertIn("/api/auth/telegram", paths)

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
        self.assertFalse(_marketplace_visibility["mrkt"])


if __name__ == "__main__":
    unittest.main()
