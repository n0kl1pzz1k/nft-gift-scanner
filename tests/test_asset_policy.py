import unittest
from unittest.mock import patch

from asset_policy import ASSETS, AssetRejected, get_asset, is_crypto_payment_enabled, validate_payment_amount

TON_USDT = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
TRON_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


class AssetPolicyTests(unittest.TestCase):
    def test_crypto_assets_remain_disabled_without_full_release_configuration(self):
        self.assertTrue(ASSETS)
        self.assertTrue(ASSETS["GRAM"].payment_enabled)
        self.assertTrue(ASSETS["usdt-ton"].payment_enabled)
        self.assertFalse(ASSETS["usdt-tron"].payment_enabled)
        with patch.dict("os.environ", {}, clear=True):
            self.assertTrue(all(not is_crypto_payment_enabled(asset_id) for asset_id in ASSETS))

    def test_official_ton_usdt_identifier_is_recognized_but_disabled_without_gates(self):
        asset = get_asset("usdt-ton", "ton-mainnet", TON_USDT, "telegram-mini-app")
        self.assertEqual(asset.decimals, 6)
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(is_crypto_payment_enabled("usdt-ton"))

    def test_ton_crypto_rails_require_all_release_gates(self):
        ready = {
            "TON_CRYPTO_DEPOSITS_ENABLED": "true",
            "TON_DEPOSIT_MONITOR_ENABLED": "true",
            "MARKETPLACE_REAL_DEALS_ENABLED": "true",
            "GIFT_RADAR_STORAGE_BACKEND": "postgres",
            "DATABASE_URL": "postgresql://user:pass@db.example/giftradar",
            "TON_DEPOSIT_PROVIDER_IDS": "tonapi,toncenter",
            "TONCENTER_API_KEY": "test-key",
            "TONAPI_API_KEY": "test-tonapi-key",
            "TON_CRYPTO_EXTERNAL_CHECKOUT_APPROVED": "true",
            "TON_DEPOSIT_ADDRESS": "0:" + "11" * 32,
            "KZ_LEGAL_REVIEW_APPROVED": "true",
            "KZ_DATA_RESIDENCY_VERIFIED": "true",
            "KZ_TAX_REVIEW_APPROVED": "true",
            "KZ_SECURITY_REVIEW_APPROVED": "true",
        }
        with patch.dict("os.environ", ready, clear=True):
            self.assertFalse(is_crypto_payment_enabled("GRAM"))
            self.assertFalse(is_crypto_payment_enabled("usdt-ton"))
            self.assertTrue(is_crypto_payment_enabled("GRAM", surface="external"))
            self.assertTrue(is_crypto_payment_enabled("usdt-ton", surface="external"))
            self.assertFalse(is_crypto_payment_enabled("usdt-tron"))
        ready["KZ_DATA_RESIDENCY_VERIFIED"] = "false"
        with patch.dict("os.environ", ready, clear=True):
            self.assertFalse(is_crypto_payment_enabled("GRAM", surface="external"))

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

    def test_native_gram_is_recognized_without_a_jetton_master(self):
        asset = get_asset("GRAM", "ton-mainnet", "", "telegram-mini-app")
        self.assertEqual(asset.asset_kind, "native")
        self.assertEqual(asset.decimals, 9)
        self.assertTrue(asset.payment_enabled)
        with patch.dict('os.environ', {}, clear=True):
            self.assertFalse(is_crypto_payment_enabled('GRAM'))


    def test_anti_dust_minimums_reject_tiny_native_and_usdt_payments(self):
        with self.assertRaises(AssetRejected):
            validate_payment_amount("GRAM", 9_999_999)
        with self.assertRaises(AssetRejected):
            validate_payment_amount("usdt-ton", 99_999)
        self.assertEqual(validate_payment_amount("GRAM", 10_000_000), 10_000_000)
        self.assertEqual(validate_payment_amount("usdt-ton", 100_000), 100_000)

    def test_payment_amount_rejects_invalid_and_excessive_values(self):
        for value in (0, -1, "not-a-number", 9_000_000_000_000_001):
            with self.subTest(value=value), self.assertRaises(AssetRejected):
                validate_payment_amount("GRAM", value)

    def test_native_gram_rejects_lookalike_jetton_contract(self):
        with self.assertRaises(AssetRejected):
            get_asset("GRAM", "ton-mainnet", "EQFakeGramJetton", "telegram-mini-app")

    def test_old_gram_jetton_identifier_is_not_allowlisted(self):
        with self.assertRaises(AssetRejected):
            get_asset("gram", "ton-mainnet", "EQC47093oX5Xhb0xuk2lCr2RhS8rj-vul61u4W2UH5ORmG_O", "telegram-mini-app")


if __name__ == "__main__":
    unittest.main()
