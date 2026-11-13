import os
import unittest
from unittest.mock import patch

from app import _native_nft_escrow_monitor_enabled


class NativeNftEscrowReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.approved_environment = {
            "NATIVE_NFT_ESCROW_MONITOR_ENABLED": "true",
            "GIFT_RADAR_STORAGE_BACKEND": "postgres",
            "DATABASE_URL": "postgresql+psycopg://user:password@db.example:5432/gift_radar",
            "TON_NFT_PROVIDER_IDS": "tonapi,toncenter",
            "GIFT_RADAR_ESCROW_ADDRESS": "0:" + "11" * 32,
            "GIFT_RADAR_ESCROW_CODE_HASH": "a" * 64,
            "TONAPI_KEY": "test-tonapi-key",
            "TONCENTER_API_KEY": "test-toncenter-key",
            "KZ_LEGAL_REVIEW_APPROVED": "true",
            "KZ_DATA_RESIDENCY_VERIFIED": "true",
            "KZ_TAX_REVIEW_APPROVED": "true",
            "KZ_SECURITY_REVIEW_APPROVED": "true",
        }

    def test_monitor_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_native_nft_escrow_monitor_enabled())

    def test_monitor_requires_every_release_gate(self):
        for key in self.approved_environment:
            env = dict(self.approved_environment)
            env.pop(key)
            with self.subTest(missing=key), patch.dict(os.environ, env, clear=True):
                self.assertFalse(_native_nft_escrow_monitor_enabled())

    def test_monitor_requires_exact_independent_provider_pair(self):
        env = dict(self.approved_environment, TON_NFT_PROVIDER_IDS="tonapi")
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(_native_nft_escrow_monitor_enabled())

    def test_monitor_enables_only_when_all_release_gates_are_explicit(self):
        with patch.dict(os.environ, self.approved_environment, clear=True):
            self.assertTrue(_native_nft_escrow_monitor_enabled())


if __name__ == "__main__":
    unittest.main()
