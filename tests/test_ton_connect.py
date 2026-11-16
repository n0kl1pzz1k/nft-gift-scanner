import base64
import hashlib
import os
import tempfile
import unittest
from unittest.mock import patch

from nacl.signing import SigningKey

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

    def test_network_must_be_enabled(self):
        with self.assertRaisesRegex(ValueError, "not enabled"):
            self._verify(network="-3")


if __name__ == "__main__":
    unittest.main()
