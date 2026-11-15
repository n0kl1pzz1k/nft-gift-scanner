import unittest

from asset_policy import ASSETS, AssetRejected, get_asset, validate_payment_amount


class TonOnlyAssetPolicyTests(unittest.TestCase):
    def test_only_native_ton_is_allowlisted(self):
        self.assertEqual(set(ASSETS), {"TON"})
        asset = get_asset("TON", "ton-mainnet", "")
        self.assertEqual(asset.display_symbol, "TON")
        self.assertEqual(asset.asset_kind, "native")

    def test_jettons_and_other_networks_are_rejected(self):
        for asset_id, chain, master in (
            ("UNSUPPORTED_TOKEN", "ton-mainnet", "EQ" + "1" * 46),
            ("UNSUPPORTED_NATIVE_ALIAS", "ton-mainnet", "EQ" + "2" * 46),
            ("TON", "non-ton-network", ""),
            ("TON", "ton-mainnet", "EQ" + "3" * 46),
        ):
            with self.subTest(asset_id=asset_id, chain=chain):
                with self.assertRaises(AssetRejected):
                    get_asset(asset_id, chain, master)

    def test_native_ton_amount_validation(self):
        self.assertEqual(validate_payment_amount("TON", 1), 1)
        for amount in (0, -1, "not-an-amount"):
            with self.subTest(amount=amount), self.assertRaises(AssetRejected):
                validate_payment_amount("TON", amount)
        with self.assertRaises(AssetRejected):
            validate_payment_amount("UNSUPPORTED_NATIVE_ALIAS", 1)


if __name__ == "__main__":
    unittest.main()
