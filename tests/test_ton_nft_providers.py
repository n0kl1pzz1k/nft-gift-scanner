import os
import unittest
from unittest.mock import patch

from ton_nft_providers import canonical_ton_address, configured_nft_providers


class TonNftProviderTests(unittest.TestCase):
    def test_friendly_and_raw_addresses_normalize_to_same_canonical_identity(self):
        friendly = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
        raw = "0:b113a994b5024a16719f69139328eb759596c38a25f59028b146fecdc3621dfe"
        self.assertEqual(canonical_ton_address(friendly), raw)
        self.assertEqual(canonical_ton_address(raw), raw)

    def test_friendly_address_checksum_is_verified(self):
        with self.assertRaises(ValueError):
            canonical_ton_address("EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDt")

    def test_test_only_friendly_address_is_rejected_on_mainnet(self):
        test_only = "kQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
        with self.assertRaises(ValueError):
            canonical_ton_address(test_only)

    def test_raw_address_rejects_invalid_length_and_workchain(self):
        with self.assertRaises(ValueError):
            canonical_ton_address("0:abcd")
        with self.assertRaises(ValueError):
            canonical_ton_address("128:" + "11" * 32)

    def test_provider_quorum_requires_two_named_independent_operators_and_keys(self):
        with patch.dict(os.environ, {
            "TON_NFT_PROVIDER_IDS": "tonapi,toncenter",
            "TONAPI_KEY": "test-tonapi-key",
            "TONCENTER_API_KEY": "test-toncenter-key",
        }):
            providers = configured_nft_providers()
            self.assertEqual({provider.provider_id for provider in providers}, {"tonapi", "toncenter"})
        with patch.dict(os.environ, {
            "TON_NFT_PROVIDER_IDS": "tonapi,alias",
            "TONAPI_KEY": "test-tonapi-key",
            "TONCENTER_API_KEY": "test-toncenter-key",
        }):
            with self.assertRaises(RuntimeError):
                configured_nft_providers()


if __name__ == "__main__":
    unittest.main()
