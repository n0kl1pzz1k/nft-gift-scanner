import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, insert

from financial_ledger import withdrawal_requests, post_journal_entry, get_account_balance
from withdrawal_signer import (approve_withdrawal, create_withdrawal_request, init_withdrawal_signer_schema, RemoteHsmSigner,
                                _require_matching_message_hash)


class WithdrawalApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'withdrawals.sqlite3'}", future=True)
        init_withdrawal_signer_schema(self.engine)
        with self.engine.begin() as conn:
            conn.execute(insert(withdrawal_requests).values(
                request_id="withdrawal-0001", user_id="12345", asset_id="GRAM",
                network="ton-mainnet", destination_address="0:" + "11" * 32,
                amount_raw="1000000000", fee_raw="10000000", status="requested",
                idempotency_key="withdrawal-test-idempotency-0001",
                request_hash="a" * 64, created_at=100, updated_at=100,
            ))

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def _withdrawal_release_env(self):
        return {
            "TON_WITHDRAWAL_REQUESTS_ENABLED": "true",
            "TON_WITHDRAWALS_ENABLED": "true",
            "TON_WITHDRAWAL_EXECUTOR_ENABLED": "true",
            "TON_SIGNER_SERVICE_URL": "https://hsm.example",
            "TON_SIGNER_SERVICE_TOKEN": "test-token",
            "TON_SIGNER_WALLET_ID": "test-wallet",
            "WITHDRAWAL_APPROVER_IDS": "90001,90002",
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
            "TON_WITHDRAWAL_FEE_GRAM_RAW": "10000000",
        }

    def test_withdrawal_request_reserves_balance_and_is_idempotent(self):
        post_journal_entry(
            idempotency_key="test-withdrawal-funding-0001",
            source_type="test_funding", source_reference="user-12345",
            postings=[
                {"account_id": "asset:clearing:GRAM", "account_type": "asset",
                 "currency": "GRAM", "amount_raw": 1_000_000_000},
                {"account_id": "liability:user:12345:GRAM", "account_type": "liability",
                 "currency": "GRAM", "amount_raw": -1_000_000_000},
            ], engine=self.engine,
        )
        with patch.dict(os.environ, self._withdrawal_release_env()):
            first = create_withdrawal_request(
                user_id="12345", asset_id="GRAM",
                destination_address="0:" + "22" * 32,
                amount_raw=100_000_000,
                idempotency_key="withdrawal-create-idempotency-0001",
                engine=self.engine, now=120,
            )
            duplicate = create_withdrawal_request(
                user_id="12345", asset_id="GRAM",
                destination_address="0:" + "22" * 32,
                amount_raw=100_000_000,
                idempotency_key="withdrawal-create-idempotency-0001",
                engine=self.engine, now=121,
            )
        self.assertEqual(first["status"], "requested")
        self.assertFalse(first["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(first["request_id"], duplicate["request_id"])
        self.assertEqual(first["fee_raw"], 10_000_000)
        self.assertEqual(get_account_balance(
            "liability:user:12345:GRAM", "GRAM", self.engine
        ), -890_000_000)
        self.assertEqual(get_account_balance(
            f"liability:withdrawal_hold:{first['request_id']}:GRAM", "GRAM", self.engine
        ), -100_000_000)

    def test_withdrawal_request_cannot_overdraw_or_reuse_idempotency_key(self):
        post_journal_entry(
            idempotency_key="test-withdrawal-funding-0002",
            source_type="test_funding", source_reference="user-12345",
            postings=[
                {"account_id": "asset:clearing:GRAM", "account_type": "asset",
                 "currency": "GRAM", "amount_raw": 100_000_000},
                {"account_id": "liability:user:12345:GRAM", "account_type": "liability",
                 "currency": "GRAM", "amount_raw": -100_000_000},
            ], engine=self.engine,
        )
        with patch.dict(os.environ, self._withdrawal_release_env()):
            with self.assertRaises(PermissionError):
                create_withdrawal_request(
                    user_id="12345", asset_id="GRAM",
                    destination_address="0:" + "22" * 32,
                    amount_raw=200_000_000,
                    idempotency_key="withdrawal-overdraw-idempotency-0001",
                    engine=self.engine, now=120,
                )
            create_withdrawal_request(
                user_id="12345", asset_id="GRAM",
                destination_address="0:" + "22" * 32,
                amount_raw=50_000_000,
                idempotency_key="withdrawal-reuse-idempotency-0001",
                engine=self.engine, now=121,
            )
            with self.assertRaises(ValueError):
                create_withdrawal_request(
                    user_id="12345", asset_id="GRAM",
                    destination_address="0:" + "33" * 32,
                    amount_raw=50_000_000,
                    idempotency_key="withdrawal-reuse-idempotency-0001",
                    engine=self.engine, now=122,
                )

    def test_two_distinct_allowlisted_admins_are_required(self):
        with patch.dict(os.environ, {"WITHDRAWAL_APPROVER_IDS": "90001,90002"}):
            first = approve_withdrawal(
                request_id="withdrawal-0001", admin_user_id="90001",
                engine=self.engine, now=110,
            )
            self.assertFalse(first["ready"])
            self.assertEqual(first["approval_count"], 1)
            duplicate = approve_withdrawal(
                request_id="withdrawal-0001", admin_user_id="90001",
                engine=self.engine, now=111,
            )
            self.assertEqual(duplicate["approval_count"], 1)
            second = approve_withdrawal(
                request_id="withdrawal-0001", admin_user_id="90002",
                engine=self.engine, now=112,
            )
            self.assertTrue(second["ready"])
            self.assertEqual(second["approval_count"], 2)

    def test_request_owner_cannot_approve_own_withdrawal(self):
        with patch.dict(os.environ, {"WITHDRAWAL_APPROVER_IDS": "12345,90001"}):
            with self.assertRaises(PermissionError):
                approve_withdrawal(
                    request_id="withdrawal-0001", admin_user_id="12345",
                    engine=self.engine, now=110,
                )

    def test_signer_never_accepts_insecure_endpoint(self):
        with self.assertRaises(RuntimeError):
            RemoteHsmSigner(base_url="http://signer.internal", token="not-a-real-token")

    def test_broadcast_hash_must_match_exact_hsm_signed_message(self):
        message_hash = "a" * 64
        self.assertEqual(
            _require_matching_message_hash("0x" + message_hash.upper(), message_hash),
            message_hash,
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            _require_matching_message_hash(message_hash, "b" * 64)

    def test_malformed_broadcast_hash_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not canonical"):
            _require_matching_message_hash("a" * 64, "not-a-hash")


if __name__ == "__main__":
    unittest.main()
