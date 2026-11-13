import unittest

from security_controls import _valid_origin, _requires_edge_limit, _apply_security_headers
from starlette.responses import Response


class SecurityControlTests(unittest.TestCase):
    def test_https_origin_is_required_and_exactly_allowlisted(self):
        self.assertTrue(_valid_origin("https://nft-gift-scanner.pages.dev"))
        self.assertFalse(_valid_origin("http://nft-gift-scanner.pages.dev"))
        self.assertFalse(_valid_origin("https://nft-gift-scanner.pages.dev.evil.example"))
        self.assertFalse(_valid_origin("https://evil.example"))

    def test_every_api_route_including_public_reads_and_admin_routes_is_rate_limited(self):
        for path in (
            "/api/inventory/nft/import", "/api/inventory/listings",
            "/api/inventory/mine", "/api/collections", "/api/status",
            "/api/admin/withdrawals", "/api/payments/capabilities",
            "/api/telegram/webhook",
        ):
            with self.subTest(path=path):
                self.assertTrue(_requires_edge_limit(path))
        self.assertFalse(_requires_edge_limit("/health"))
        self.assertFalse(_requires_edge_limit("/favicon.ico"))

    def test_api_responses_receive_security_and_no_store_headers(self):
        response = _apply_security_headers(Response(content="{}", media_type="application/json"))
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("max-age=", response.headers["Strict-Transport-Security"])

    def test_origin_with_path_or_nonstandard_port_is_rejected(self):
        self.assertFalse(_valid_origin("https://nft-gift-scanner.pages.dev/path"))
        self.assertFalse(_valid_origin("https://nft-gift-scanner.pages.dev:8443"))


if __name__ == "__main__":
    unittest.main()
