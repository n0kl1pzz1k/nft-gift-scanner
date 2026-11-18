import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

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

    def test_maintenance_mode_rejects_writes_but_keeps_health_available(self):
        client = TestClient(app)
        with patch.dict("os.environ", {"HASSLE_MAINTENANCE_MODE": "1"}, clear=False):
            write_response = client.post(
                "/api/auth/telegram",
                headers={"Origin": "https://hassle-nft-market.fj7kmzkhn9.workers.dev"},
                json={"init_data": "not-used-during-maintenance"},
            )
            health_response = client.get("/health")

        self.assertEqual(write_response.status_code, 503)
        self.assertEqual(write_response.headers.get("Retry-After"), "30")
        self.assertTrue(write_response.json()["maintenance"])
        self.assertEqual(
            write_response.headers.get("Access-Control-Allow-Origin"),
            "https://hassle-nft-market.fj7kmzkhn9.workers.dev",
        )
        self.assertEqual(health_response.status_code, 200)

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
