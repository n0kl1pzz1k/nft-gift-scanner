import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app, _validate_cors_origins


class ApiContractTests(unittest.TestCase):
    def test_scanner_catalogue_routes_are_removed(self):
        paths = set(app.openapi()['paths'])
        self.assertIn("/health", paths)
        self.assertIn("/api/auth/telegram", paths)
        self.assertIn("/api/marketplace/orders", paths)
        self.assertIn("/api/marketplace/stats", paths)
        self.assertIn("/api/marketplace/deals/{deal_id}/ton-nft-delivery/prepare", paths)
        self.assertIn("/api/marketplace/deals/{deal_id}/ton-nft-delivery/confirm", paths)
        self.assertIn("/api/marketplace/deals/{deal_id}/ton-nft-delivery/submitted", paths)
        self.assertIn("/api/telegram/webhook", paths)
        self.assertNotIn("/api/status", paths)
        self.assertNotIn("/api/collections", paths)
        self.assertNotIn("/api/gifts", paths)
        self.assertNotIn("/api/catalog/items", paths)
        self.assertNotIn("/api/reference/gift-changes", paths)
        self.assertNotIn("/api/reference/daily-floors", paths)

    def test_administrator_api_surface_is_limited_to_moderation_and_audit(self):
        admin_paths = {
            path for path in app.openapi()["paths"]
            if "/admin/" in path
        }
        self.assertEqual(
            admin_paths,
            {
                "/api/marketplace/admin/reports",
                "/api/marketplace/admin/reports/{report_id}",
                "/api/marketplace/admin/security-audit",
                "/api/marketplace/admin/deals/{deal_id}/dispute-approvals",
                "/api/marketplace/admin/deals/{deal_id}/execute-refund",
                "/api/marketplace/admin/overview",
                "/api/marketplace/admin/users",
                "/api/marketplace/admin/users/{telegram_id}",
                "/api/marketplace/admin/roles",
                "/api/marketplace/admin/roles/{telegram_id}",
                "/api/marketplace/admin/disputes",
                "/api/marketplace/admin/reconciliation",
                "/api/marketplace/admin/reconciliation/{item_id}",
                "/api/marketplace/admin/audit",
            },
        )
        self.assertFalse(any("balance" in path or "withdraw" in path for path in admin_paths))
        self.assertFalse(any("wallet" in path for path in admin_paths))

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

    def test_cors_rejects_wildcards_http_and_path_origins(self):
        for value in (
            "*",
            "http://evil.example",
            "https://trusted.example/path",
            "https://user:password@trusted.example",
            "https://trusted.example:invalid",
        ):
            with self.subTest(origin=value):
                with self.assertRaises(RuntimeError):
                    _validate_cors_origins(value, app_env="production")

    def test_cors_accepts_only_explicit_https_origins_and_local_dev_http(self):
        self.assertEqual(
            _validate_cors_origins(
                "https://hassle.example, https://hassle.example/",
                app_env="production",
            ),
            ["https://hassle.example"],
        )
        self.assertEqual(
            _validate_cors_origins("http://localhost:5173", app_env="development"),
            ["http://localhost:5173"],
        )

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
