import unittest

from security_controls import _valid_origin, _requires_edge_limit, _apply_security_headers\nfrom starlette.responses import Response


class SecurityControlTests(unittest.TestCase):
    def test_https_origin_is_required_and_exactly_allowlisted(self):
        self.assertTrue(_valid_origin("https://nft-gift-scanner.pages.dev"))
        self.assertFalse(_valid_origin("http://nft-gift-scanner.pages.dev"))
        self.assertFalse(_valid_origin("https://nft-gift-scanner.pages.dev.evil.example"))
        self.assertFalse(_valid_origin("https://evil.example"))

    def test_inventory_mutations_use_distributed_edge_rate_limit(self):\n        self.assertTrue(_requires_edge_limit("/api/inventory/nft/import"))\n        self.assertTrue(_requires_edge_limit("/api/inventory/listings"))\n        self.assertTrue(_requires_edge_limit("/api/inventory/mine"))\n\n    def test_api_responses_receive_security_and_no_store_headers(self):
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
