import asyncio
import unittest

from payment_routes import PaymentRouteError, StarsPaymentRoute, configured_payment_route


class StarsOnlyPaymentRouteTests(unittest.TestCase):
    def test_stars_is_the_only_configured_route(self):
        route = configured_payment_route()
        self.assertIsInstance(route, StarsPaymentRoute)
        self.assertEqual(route.route_id, "stars")
        self.assertFalse(route.enabled)

    def test_checkout_must_use_telegram_invoice_and_successful_payment_update(self):
        route = StarsPaymentRoute()
        with self.assertRaisesRegex(PaymentRouteError, "Telegram Stars invoice flow"):
            asyncio.run(route.create_intent(deal_id="deal-1", amount=100, currency="XTR"))
        with self.assertRaisesRegex(PaymentRouteError, "successful_payment"):
            asyncio.run(route.verify_payment(None))


if __name__ == "__main__":
    unittest.main()
