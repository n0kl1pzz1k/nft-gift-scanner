import os
import unittest
from unittest.mock import patch

import httpx

from ton_multisig import fetch_ton_multisig_config, is_two_of_three_multisig


TREASURY = "0:" + "a" * 64
SIGNERS = ["0:" + char * 64 for char in "bcd"]


class TonMultisigVerificationTests(unittest.IsolatedAsyncioTestCase):
    def test_accepts_only_exact_two_of_three_multisig(self):
        config = {"address": TREASURY, "threshold": 2, "signers": SIGNERS}
        self.assertTrue(is_two_of_three_multisig(config, TREASURY))
        self.assertFalse(is_two_of_three_multisig(
            {**config, "threshold": 1}, TREASURY,
        ))
        self.assertFalse(is_two_of_three_multisig(
            {**config, "threshold": 2.0}, TREASURY,
        ))
        self.assertFalse(is_two_of_three_multisig(
            {**config, "signers": SIGNERS[:2]}, TREASURY,
        ))
        self.assertFalse(is_two_of_three_multisig(
            {**config, "signers": [TREASURY, SIGNERS[0], SIGNERS[1]]}, TREASURY,
        ))
        self.assertFalse(is_two_of_three_multisig(
            {**config, "signers": [SIGNERS[0], SIGNERS[0], SIGNERS[2]]}, TREASURY,
        ))
        self.assertFalse(is_two_of_three_multisig(
            {**config, "address": SIGNERS[0]}, TREASURY,
        ))
        self.assertFalse(is_two_of_three_multisig(None, TREASURY))

    async def test_fetches_multisig_configuration_from_tonapi(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={
                "address": TREASURY,
                "threshold": 2,
                "signers": SIGNERS,
                "proposers": SIGNERS,
                "orders": [],
            })
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://tonapi.io",
        )
        with patch.dict(os.environ, {"TONAPI_TOKEN": "test-token"}):
            config = await fetch_ton_multisig_config(TREASURY, client=client)
        await client.aclose()
        self.assertEqual(config["threshold"], 2)
        self.assertEqual(calls[0].url.path, f"/v2/multisig/{TREASURY}")
        self.assertEqual(calls[0].headers.get("Authorization"), "Bearer test-token")


    async def test_fetches_and_validates_wallet_public_key(self):
        calls = []
        public_key = "ab" * 32

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"public_key": public_key.upper()})

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://tonapi.io",
        )
        with patch.dict(os.environ, {"TONAPI_TOKEN": "test-token"}):
            from ton_multisig import fetch_ton_wallet_public_key
            result = await fetch_ton_wallet_public_key(TREASURY, client=client)
        await client.aclose()
        self.assertEqual(result, public_key)
        self.assertEqual(calls[0].url.path, f"/v2/accounts/{TREASURY}/publickey")
        self.assertEqual(calls[0].headers.get("Authorization"), "Bearer test-token")

    async def test_rejects_malformed_wallet_public_key(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"public_key": "not-a-key"})
            ),
            base_url="https://tonapi.io",
        )
        from ton_multisig import fetch_ton_wallet_public_key
        with self.assertRaises(ValueError):
            await fetch_ton_wallet_public_key(TREASURY, client=client)
        await client.aclose()

if __name__ == "__main__":
    unittest.main()
