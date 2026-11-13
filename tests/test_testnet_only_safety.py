import asyncio
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app import (
    _native_nft_escrow_monitor_enabled,
    _stars_payment_enabled,
    ton_proof_verify,
    TonProofVerifyRequest,
)
from asset_policy import is_crypto_payment_enabled
from release_policy import (
    TELEGRAM_STARS_TEST_ENV_READY,
    TON_TESTNET_DEPOSIT_ADAPTER_READY,
    TON_TESTNET_NFT_VERIFIER_READY,
    TON_TESTNET_WITHDRAWAL_ADAPTER_READY,
)
from ton_deposit_monitor import run_ton_deposit_monitor
from withdrawal_signer import create_withdrawal_request, execute_approved_withdrawal


class TestnetOnlySafetyTests(unittest.TestCase):
    def test_runtime_policy_is_not_overridable_by_environment_flags(self):
        self.assertTrue(TELEGRAM_STARS_TEST_ENV_READY is False)
        self.assertTrue(TON_TESTNET_DEPOSIT_ADAPTER_READY is False)
        self.assertTrue(TON_TESTNET_WITHDRAWAL_ADAPTER_READY is False)
        self.assertTrue(TON_TESTNET_NFT_VERIFIER_READY is False)

    def test_crypto_payment_remains_disabled_when_all_legacy_flags_are_true(self):
        ready = {
            "TON_CRYPTO_DEPOSITS_ENABLED": "true",
            "TON_DEPOSIT_MONITOR_ENABLED": "true",
            "MARKETPLACE_REAL_DEALS_ENABLED": "true",
            "GIFT_RADAR_STORAGE_BACKEND": "postgres",
            "DATABASE_URL": "postgresql://user:pass@db.example/giftradar",
            "TON_DEPOSIT_PROVIDER_IDS": "tonapi,toncenter",
            "TONCENTER_API_KEY": "test-key",
            "TONAPI_EMBED_URL": "https://tonapi.example",
            "TON_DEPOSIT_ADDRESS": "0:" + "11" * 32,
            "KZ_LEGAL_REVIEW_APPROVED": "true",
            "KZ_DATA_RESIDENCY_VERIFIED": "true",
            "KZ_TAX_REVIEW_APPROVED": "true",
            "KZ_SECURITY_REVIEW_APPROVED": "true",
        }
        with patch.dict(os.environ, ready, clear=True):
            self.assertFalse(is_crypto_payment_enabled("GRAM"))
            self.assertFalse(is_crypto_payment_enabled("usdt-ton"))

    def test_ton_deposit_worker_stays_inert_even_when_legacy_flags_are_true(self):
        ready = {
            "TON_DEPOSIT_MONITOR_ENABLED": "true",
            "TON_CRYPTO_DEPOSITS_ENABLED": "true",
            "MARKETPLACE_REAL_DEALS_ENABLED": "true",
            "GIFT_RADAR_STORAGE_BACKEND": "postgres",
            "DATABASE_URL": "postgresql://user:pass@db.example/giftradar",
            "KZ_LEGAL_REVIEW_APPROVED": "true",
            "KZ_DATA_RESIDENCY_VERIFIED": "true",
            "KZ_TAX_REVIEW_APPROVED": "true",
            "KZ_SECURITY_REVIEW_APPROVED": "true",
        }
        with patch.dict(os.environ, ready, clear=True):
            self.assertIsNone(asyncio.run(run_ton_deposit_monitor()))

    def test_withdrawal_request_and_executor_cannot_reach_mainnet(self):
        ready = {
            "TON_WITHDRAWAL_REQUESTS_ENABLED": "true",
            "TON_WITHDRAWALS_ENABLED": "true",
            "TON_WITHDRAWAL_EXECUTOR_ENABLED": "true",
            "TON_SIGNER_SERVICE_URL": "https://hsm.example",
            "TON_SIGNER_SERVICE_TOKEN": "test-token",
            "TON_SIGNER_WALLET_ID": "test-wallet",
            "TONCENTER_API_KEY": "test-key",
            "WITHDRAWAL_APPROVER_IDS": "90001,90002",
            "KZ_LEGAL_REVIEW_APPROVED": "true",
            "KZ_DATA_RESIDENCY_VERIFIED": "true",
            "KZ_TAX_REVIEW_APPROVED": "true",
            "KZ_SECURITY_REVIEW_APPROVED": "true",
        }
        with patch.dict(os.environ, ready, clear=True):
            with self.assertRaises(PermissionError):
                create_withdrawal_request(
                    user_id="12345", asset_id="GRAM",
                    destination_address="0:" + "22" * 32,
                    amount_raw=100_000_000,
                    idempotency_key="testnet-only-withdrawal-0001",
                )
            with self.assertRaises(PermissionError):
                asyncio.run(execute_approved_withdrawal(request_id="test-request"))

    def test_bot_stars_precheckout_gate_is_disabled_in_testnet_only_mode(self):
        import importlib
        import bot

        with patch.dict(os.environ, {
            "TELEGRAM_STARS_PAYMENTS_ENABLED": "true",
            "MARKETPLACE_REAL_DEALS_ENABLED": "true",
            "TELEGRAM_STARS_TERMS_VERSION": "test-v1",
            "GIFT_RADAR_STORAGE_BACKEND": "postgres",
            "DATABASE_URL": "postgresql://user:pass@db.example/giftradar",
            "TERMS_URL": "https://example.test/terms",
            "PAYMENT_SUPPORT_URL": "https://example.test/support",
        }, clear=True):
            importlib.reload(bot)
            self.assertFalse(bot.STARS_PAYMENTS_ENABLED)

    def test_stars_payment_and_refund_are_disabled_without_test_bot_environment(self):
        ready = {
            "TELEGRAM_STARS_PAYMENTS_ENABLED": "true",
            "MARKETPLACE_REAL_DEALS_ENABLED": "true",
            "TELEGRAM_STARS_TERMS_VERSION": "test-v1",
            "GIFT_RADAR_STORAGE_BACKEND": "postgres",
            "DATABASE_URL": "postgresql://user:pass@db.example/giftradar",
            "TERMS_URL": "https://example.test/terms",
            "PAYMENT_SUPPORT_URL": "https://example.test/support",
            "KZ_LEGAL_REVIEW_APPROVED": "true",
            "KZ_DATA_RESIDENCY_VERIFIED": "true",
            "KZ_TAX_REVIEW_APPROVED": "true",
            "KZ_SECURITY_REVIEW_APPROVED": "true",
        }
        with patch.dict(os.environ, ready, clear=True):
            self.assertFalse(_stars_payment_enabled())

    def test_nft_mainnet_import_and_escrow_monitor_remain_disabled(self):
        ready = {
            "NATIVE_NFT_ESCROW_MONITOR_ENABLED": "true",
            "GIFT_RADAR_STORAGE_BACKEND": "postgres",
            "DATABASE_URL": "postgresql://user:pass@db.example/giftradar",
            "TON_NFT_PROVIDER_IDS": "tonapi,toncenter",
            "GIFT_RADAR_ESCROW_ADDRESS": "0:" + "11" * 32,
            "GIFT_RADAR_ESCROW_CODE_HASH": "a" * 64,
            "TONAPI_KEY": "test-key",
            "TONCENTER_API_KEY": "test-key",
            "KZ_LEGAL_REVIEW_APPROVED": "true",
            "KZ_DATA_RESIDENCY_VERIFIED": "true",
            "KZ_TAX_REVIEW_APPROVED": "true",
            "KZ_SECURITY_REVIEW_APPROVED": "true",
        }
        with patch.dict(os.environ, ready, clear=True):
            self.assertFalse(_native_nft_escrow_monitor_enabled())

    def test_mainnet_ton_proof_is_rejected_before_external_call(self):
        payload = TonProofVerifyRequest(
            address="0:" + "11" * 32,
            network="-239",
            state_init="not-used",
            proof={},
        )
        with patch.dict(os.environ, {
            "TON_PROOF_SERVICE_URL": "https://ton-proof.example",
            "TON_PROOF_INTERNAL_TOKEN": "test-token",
        }, clear=True), patch("app._authenticate_telegram_credential", return_value={"id": "12345"}):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(ton_proof_verify(payload, "grs_test_session"))
        self.assertEqual(caught.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
