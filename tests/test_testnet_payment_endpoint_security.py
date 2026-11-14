import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as app_module
from payment_routes import PaymentRouteError, configured_payment_route


class TestnetPaymentEndpointSecurityTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app)

    def test_payment_intent_requires_telegram_authentication(self):
        with patch.object(
            app_module, "_authenticate_telegram_credential",
            side_effect=HTTPException(status_code=401, detail="Unauthorized"),
        ):
            response = self.client.post(
                "/api/inventory/deals/deal-test/testnet-payment-intent"
            )
        self.assertEqual(response.status_code, 401)

    def test_payment_confirmation_requires_platform_admin_authentication(self):
        with patch.object(
            app_module, "_authenticate_telegram_credential",
            return_value={"id": "202"},
        ), patch.object(
            app_module, "_require_admin",
            side_effect=HTTPException(status_code=403, detail="Forbidden"),
        ):
            response = self.client.post(
                "/api/inventory/deals/deal-test/testnet-payment-confirmation"
            )
        self.assertEqual(response.status_code, 403)

    def test_testnet_intent_fails_closed_when_route_is_not_testnet(self):
        with patch.object(
            app_module, "_authenticate_telegram_credential",
            return_value={"id": "202"},
        ), patch(
            "payment_routes.configured_payment_route",
            side_effect=PaymentRouteError("Unsupported payment route: mainnet"),
        ):
            response = self.client.post(
                "/api/inventory/deals/deal-test/testnet-payment-intent"
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("mainnet", response.text.lower())

    def test_payment_route_factory_rejects_mainnet_network(self):
        class Gateway:
            async def create_intent(self, **kwargs):
                raise AssertionError("No network operation should be attempted")

            async def verify_payment(self, intent):
                raise AssertionError("No network operation should be attempted")

            async def refund(self, evidence):
                raise AssertionError("No network operation should be attempted")

        with patch.dict("os.environ", {
            "PAYMENT_ROUTE": "ton_usdt_testnet",
            "TON_NETWORK": "mainnet",
            "TON_TESTNET_ESCROW_ADDRESS": "0:" + "11" * 32,
            "TON_TESTNET_USDT_MASTER": "0:" + "22" * 32,
        }):
            with self.assertRaisesRegex(PaymentRouteError, "testnet-only"):
                configured_payment_route(ton_gateway=Gateway())


if __name__ == "__main__":
    unittest.main()
