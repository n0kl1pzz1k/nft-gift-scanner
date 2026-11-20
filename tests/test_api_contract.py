import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app, _validate_cors_origins, _READINESS_TABLES, _READINESS_COLUMNS


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

    def test_administrator_api_surface_is_limited_to_moderation_disputes_and_settlement(self):
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
                "/api/marketplace/admin/overview",
                "/api/marketplace/admin/roles/me",
                "/api/marketplace/admin/users",
                "/api/marketplace/admin/users/{telegram_id}/revoke-sessions",
                "/api/marketplace/admin/assets",
                "/api/marketplace/admin/assets/{asset_id}/revoke",
                "/api/marketplace/admin/reconciliation",
                "/api/marketplace/admin/deals",
                "/api/marketplace/admin/deals/{deal_id}/dispute-approvals",
                "/api/marketplace/admin/deals/{deal_id}/execute-refund",
                "/api/marketplace/admin/deals/{deal_id}/execute-ton-refund",
                "/api/marketplace/admin/deals/{deal_id}/execute-seller-release",
                "/api/marketplace/admin/deals/{deal_id}/execute-ton-seller-payout",
            },
        )
        self.assertFalse(any("balance" in path or "withdraw" in path for path in admin_paths))
        self.assertFalse(any("wallet" in path for path in admin_paths))

    def test_readiness_requires_payment_ledger_escrow_and_dispute_schema(self):
        required_tables = {
            "marketplace_payment_intents",
            "marketplace_payment_events",
            "marketplace_ledger_transactions",
            "marketplace_ledger_entries",
            "marketplace_escrow_events",
            "marketplace_dispute_resolution_approvals",
        }
        self.assertTrue(required_tables.issubset(set(_READINESS_TABLES)))
        required_columns = {
            "marketplace_payment_intents": {
                "id", "deal_id", "provider", "currency", "amount", "status",
                "idempotency_key", "expires_at", "created_at", "updated_at",
            },
            "marketplace_payment_events": {
                "id", "provider", "provider_event_id", "payment_intent_id",
                "event_type", "payload_hash", "created_at",
            },
            "marketplace_ledger_transactions": {
                "id", "idempotency_key", "reference_type", "reference_id",
                "currency", "amount", "created_at",
            },
            "marketplace_ledger_entries": {
                "id", "transaction_id", "account_type", "account_id",
                "direction", "currency", "amount", "created_at",
            },
            "marketplace_escrow_events": {
                "id", "deal_id", "actor_user_id", "event_type", "event_key",
                "event_data", "created_at",
            },
            "marketplace_dispute_resolution_approvals": {
                "id", "deal_id", "admin_user_id", "decision",
                "idempotency_key", "created_at",
            },
        }
        for table, columns in required_columns.items():
            with self.subTest(table=table):
                self.assertTrue(columns.issubset(_READINESS_COLUMNS[table]))

    def test_liveness_and_readiness_routes_are_registered_separately(self):
        paths = set(app.openapi()["paths"])
        self.assertIn("/health", paths)
        self.assertIn("/ready", paths)
        self.assertNotEqual(app.openapi()["paths"]["/health"]["get"]["summary"],
                            app.openapi()["paths"]["/ready"]["get"]["summary"])

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
