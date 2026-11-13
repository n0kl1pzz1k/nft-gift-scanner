import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, insert

from financial_ledger import init_financial_schema, withdrawal_requests
from withdrawal_signer import approve_withdrawal, init_withdrawal_signer_schema, RemoteHsmSigner


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


if __name__ == "__main__":
    unittest.main()
