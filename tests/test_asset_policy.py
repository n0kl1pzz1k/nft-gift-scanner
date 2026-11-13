import unittest

from asset_policy import ASSETS, AssetRejected, get_asset, is_crypto_payment_enabled

TON_USDT = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
TRON_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


class AssetPolicyTests(unittest.TestCase):
    def test_every_allowlisted_crypto_asset_is_nonpayable_by_default(self):
        self.assertTrue(ASSETS)
        self.assertTrue(all(asset.payment_enabled is False for asset in ASSETS.values()))
        self.assertTrue(all(not is_crypto_payment_enabled(asset_id) for asset_id in ASSETS))

    def test_official_ton_usdt_identifier_is_recognized_but_not_payable(self):
        asset = get_asset("usdt-ton", "ton-mainnet", TON_USDT, "telegram-mini-app")
        self.assertEqual(asset.decimals, 6)
        self.assertFalse(is_crypto_payment_enabled("usdt-ton"))

    def test_lookalike_contract_with_same_ticker_is_rejected(self):
        with self.assertRaises(AssetRejected):
            get_asset("usdt-ton", "ton-mainnet", "EQFakeLookalikeContract", "telegram-mini-app")

    def test_tron_usdt_is_rejected_inside_mini_app(self):
        with self.assertRaises(AssetRejected):
            get_asset("usdt-tron", "tron-mainnet", TRON_USDT, "telegram-mini-app")

    def test_tron_usdt_is_not_enabled_for_external_payments_either(self):
        asset = get_asset("usdt-tron", "tron-mainnet", TRON_USDT, "external")
        self.assertFalse(asset.payment_enabled)
        self.assertFalse(is_crypto_payment_enabled("usdt-tron"))

    def test_wrong_network_is_rejected(self):
        with self.assertRaises(AssetRejected):
            get_asset("usdt-ton", "tron-mainnet", TON_USDT, "external")

    def test_unapproved_gram_is_rejected(self):
        with self.assertRaises(AssetRejected):
            get_asset("gram", "ton-mainnet", "unknown", "telegram-mini-app")


if __name__ == "__main__":
    unittest.main()
