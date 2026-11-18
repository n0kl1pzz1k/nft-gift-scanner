import base64
from contextlib import contextmanager
import hashlib
import os
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

from nacl.signing import SigningKey
from tonsdk.boc import Cell

import marketplace
import ton_connect


class TonConnectProofTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(marketplace, "DB_PATH", os.path.join(self.temp_dir.name, "ton.sqlite3"))
        self.db_patch.start()
        ton_connect.init_ton_connect_db()
        self.session = "grs_test-session-token"
        self.user_id = "101"
        self.signing_key = SigningKey.generate()
        self.public_key = bytes(self.signing_key.verify_key)
        self.address_hash = hashlib.sha256(b"address-bound-state-init").digest()
        self.address = "0:" + self.address_hash.hex()
        self.domain = "hassle-nft-market.fj7kmzkhn9.workers.dev"
        self.now = 1_800_000_000
        self.payload = ton_connect.create_proof_challenge(self.user_id, self.session, now=self.now)
        self.proof = self._signed_proof(self.payload)

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _signed_proof(self, payload, **overrides):
        timestamp = self.now
        domain_bytes = self.domain.encode("utf-8")
        message = (
            b"ton-proof-item-v2/"
            + (0).to_bytes(4, "big", signed=True)
            + self.address_hash
            + len(domain_bytes).to_bytes(4, "little")
            + domain_bytes
            + timestamp.to_bytes(8, "little")
            + payload.encode("utf-8")
        )
        digest = hashlib.sha256(b"\xff\xffton-connect" + hashlib.sha256(message).digest()).digest()
        signature = base64.b64encode(self.signing_key.sign(digest).signature).decode("ascii")
        proof = {
            "timestamp": timestamp,
            "domain": {"lengthBytes": len(domain_bytes), "value": self.domain},
            "payload": payload,
            "signature": signature,
        }
        proof.update(overrides)
        return proof

    def _verify(self, proof=None, network="-239", public_key_hex=None):
        with patch.object(ton_connect, "_parse_address", return_value=(0, self.address_hash, self.address)), patch.object(
            ton_connect, "_parse_state_init",
            return_value=(self.address_hash, self.public_key, "feb5ff6820e2ff0d9483e7e0d62c817d846789fb4ae580c878866d959dabd5c0"),
        ):
            return ton_connect.verify_ton_proof(
                self.address, network, public_key_hex or self.public_key.hex(),
                proof or self.proof, "ZmFrZS1zdGF0ZS1pbml0", self.domain, now=self.now,
            )

    def test_valid_proof_is_verified_and_challenge_can_be_consumed_once(self):
        verified = self._verify()
        self.assertEqual(verified["address"], self.address)
        self.assertEqual(verified["network"], "-239")
        self.assertTrue(ton_connect.consume_challenge_and_link_wallet(verified, self.user_id, self.session, now=self.now))
        self.assertFalse(ton_connect.consume_challenge_and_link_wallet(verified, self.user_id, self.session, now=self.now))
        wallets = ton_connect.list_verified_wallets(self.user_id)
        self.assertEqual(len(wallets), 1)
        self.assertEqual(wallets[0]["address"], self.address)

    def test_wallet_link_failure_does_not_consume_ton_proof_challenge(self):
        verified = self._verify()
        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_verified_wallet_insert
                   BEFORE INSERT ON verified_wallets
                   BEGIN SELECT RAISE(ABORT, 'injected wallet persistence failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError) as raised:
            ton_connect.consume_challenge_and_link_wallet(
                verified, self.user_id, self.session, now=self.now,
            )
        self.assertIn("injected wallet persistence failure", str(raised.exception))

        with marketplace._db() as conn:
            challenge = conn.execute(
                "SELECT consumed_at FROM ton_proof_challenges WHERE telegram_id = ?",
                (self.user_id,),
            ).fetchone()
            wallet_count = conn.execute(
                "SELECT COUNT(*) FROM verified_wallets WHERE telegram_id = ?",
                (self.user_id,),
            ).fetchone()[0]
        self.assertIsNone(challenge["consumed_at"])
        self.assertEqual(wallet_count, 0)

        with marketplace._db() as conn:
            conn.execute("DROP TRIGGER fail_verified_wallet_insert")
        self.assertTrue(
            ton_connect.consume_challenge_and_link_wallet(
                verified, self.user_id, self.session, now=self.now,
            )
        )
        self.assertEqual(len(ton_connect.list_verified_wallets(self.user_id)), 1)

    def test_failed_challenge_replacement_preserves_previous_valid_challenge(self):
        original_payload_hash = hashlib.sha256(self.payload.encode("utf-8")).hexdigest()
        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_ton_challenge_insert
                   BEFORE INSERT ON ton_proof_challenges
                   BEGIN SELECT RAISE(ABORT, 'injected challenge persistence failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            ton_connect.create_proof_challenge(
                self.user_id, self.session, now=self.now + 1,
            )

        with marketplace._db() as conn:
            challenge = conn.execute(
                """SELECT consumed_at FROM ton_proof_challenges
                   WHERE payload_hash = ? AND telegram_id = ?""",
                (original_payload_hash, self.user_id),
            ).fetchone()
            challenge_count = conn.execute(
                "SELECT COUNT(*) FROM ton_proof_challenges WHERE telegram_id = ?",
                (self.user_id,),
            ).fetchone()[0]
        self.assertIsNotNone(challenge)
        self.assertIsNone(challenge["consumed_at"])
        self.assertEqual(challenge_count, 1)

        with marketplace._db() as conn:
            conn.execute("DROP TRIGGER fail_ton_challenge_insert")
        verified = self._verify()
        self.assertTrue(
            ton_connect.consume_challenge_and_link_wallet(
                verified, self.user_id, self.session, now=self.now + 1,
            )
        )

    def test_proof_rejects_wrong_domain(self):
        bad = dict(self.proof)
        bad["domain"] = {"lengthBytes": len(b"evil.example"), "value": "evil.example"}
        with self.assertRaisesRegex(ValueError, "domain mismatch"):
            self._verify(bad)

    def test_proof_rejects_tampered_signature(self):
        bad = dict(self.proof)
        bad["signature"] = base64.b64encode(b"\x00" * 64).decode("ascii")
        with self.assertRaisesRegex(ValueError, "signature verification failed"):
            self._verify(bad)

    def test_proof_rejects_expired_timestamp(self):
        bad = dict(self.proof)
        bad["timestamp"] = self.now - ton_connect.PROOF_MAX_AGE_SECONDS - 1
        with self.assertRaisesRegex(ValueError, "expired"):
            self._verify(bad)

    def test_proof_rejects_wallet_public_key_mismatch(self):
        other_key = SigningKey.generate()
        with self.assertRaisesRegex(ValueError, "does not match StateInit"):
            self._verify(public_key_hex=bytes(other_key.verify_key).hex())

    def test_challenge_is_bound_to_telegram_session_and_user(self):
        verified = self._verify()
        self.assertFalse(ton_connect.consume_challenge_and_link_wallet(verified, self.user_id, "grs_another-session", now=self.now))
        self.assertFalse(ton_connect.consume_challenge_and_link_wallet(verified, "202", self.session, now=self.now))

    def test_state_init_parser_uses_real_tonsdk_slice_layout(self):
        code = Cell()
        code.bits.write_uint(123, 8)
        data = Cell()
        data.bits.write_uint(0, 32)
        data.bits.write_bytes(self.public_key)
        state_init = Cell()
        state_init.bits.write_bit(0)  # no split depth
        state_init.bits.write_bit(0)  # no special
        state_init.bits.write_bit(1)  # code ref follows
        state_init.refs.append(code)
        state_init.bits.write_bit(1)  # data ref follows
        state_init.refs.append(data)
        state_init.bits.write_bit(0)  # empty library
        state_init_b64 = base64.b64encode(state_init.to_boc(has_idx=False)).decode("ascii")
        code_hash = code.bytes_hash().hex()
        with patch.dict(ton_connect._WALLET_LAYOUTS, {code_hash: 1}):
            state_hash, extracted_key, parsed_code_hash = ton_connect._parse_state_init(state_init_b64)
        self.assertEqual(state_hash, state_init.bytes_hash())
        self.assertEqual(extracted_key, self.public_key)
        self.assertEqual(parsed_code_hash, code_hash)

    def test_ton_connect_schema_runs_on_postgres_adapter_when_configured(self):
        dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN", "").strip()
        if not dsn:
            self.skipTest("HASSLE_TEST_POSTGRES_DSN is not configured")

        from database import connect_postgres_database, list_database_tables

        connection = connect_postgres_database(dsn)
        schema = "ton_connect_test_" + uuid.uuid4().hex
        schema_created = False
        try:
            connection.execute(f'CREATE SCHEMA "{schema}"')
            schema_created = True
            connection.execute(f'SET search_path TO "{schema}"')
            schema_report = ton_connect.ensure_ton_connect_schema(connection)
            self.assertTrue(schema_report["verified"])
            self.assertEqual(schema_report["tables_verified"], 2)
            tables = list_database_tables(connection)
            self.assertTrue({"ton_proof_challenges", "verified_wallets"}.issubset(tables))

            @contextmanager
            def postgres_db():
                yield connection

            with patch.object(ton_connect, "_db", side_effect=postgres_db):
                payload = ton_connect.create_proof_challenge(
                    self.user_id, self.session, now=self.now,
                )
                proof = self._signed_proof(payload)
                verified = self._verify(proof)
                self.assertTrue(ton_connect.consume_challenge_and_link_wallet(
                    verified, self.user_id, self.session, now=self.now,
                ))
                wallets = ton_connect.list_verified_wallets(self.user_id)
                self.assertEqual(len(wallets), 1)
                self.assertEqual(wallets[0]["address"], self.address)

            connection.execute("ALTER TABLE verified_wallets DROP COLUMN public_key")
            with self.assertRaisesRegex(RuntimeError, "missing columns in verified_wallets: public_key"):
                ton_connect.verify_ton_connect_schema(connection)
        finally:
            if schema_created:
                connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
            connection.close()

    def test_network_must_be_enabled(self):
        with self.assertRaisesRegex(ValueError, "not enabled"):
            self._verify(network="-3")


if __name__ == "__main__":
    unittest.main()
