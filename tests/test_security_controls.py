import unittest

from security_controls import (
    RequestBodyLimitMiddleware,
    _valid_origin,
    _requires_edge_limit,
    _apply_security_headers,
)
from starlette.responses import Response


class RequestBodyLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_content_length_over_limit_is_rejected_before_application(self):
        called = False

        async def app(scope, receive, send):
            nonlocal called
            called = True

        middleware = RequestBodyLimitMiddleware(app, max_body_bytes=4)
        scope = {
            "type": "http", "method": "POST", "path": "/api/orders",
            "headers": [(b"content-length", b"5")],
        }
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"12345", "more_body": False}

        async def send(message):
            sent.append(message)

        await middleware(scope, receive, send)
        self.assertFalse(called)
        self.assertEqual(sent[0]["status"], 413)
        headers = dict(sent[0]["headers"])
        self.assertEqual(headers[b"cache-control"], b"no-store")

    async def test_chunked_body_over_limit_is_rejected(self):
        called = False

        async def app(scope, receive, send):
            nonlocal called
            called = True

        middleware = RequestBodyLimitMiddleware(app, max_body_bytes=4)
        scope = {"type": "http", "method": "PATCH", "path": "/api/profile", "headers": []}
        chunks = iter([
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ])
        sent = []

        async def receive():
            return next(chunks)

        async def send(message):
            sent.append(message)

        await middleware(scope, receive, send)
        self.assertFalse(called)
        self.assertEqual(sent[0]["status"], 413)


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
