import base64
import os
import unittest
from unittest.mock import patch

import httpx

from tonsdk.boc import Builder, Cell, begin_dict
from tonsdk.utils import Address

from ton_multisig import (
    decode_multisig_v2_getter,
    fetch_ton_multisig_config,
    is_two_of_three_multisig,
)


TREASURY = "0:" + "a" * 64
SIGNERS = ["0:" + char * 64 for char in "bcd"]


def dictionary_boc(addresses):
    dictionary = begin_dict(8)
    for index, address in enumerate(addresses):
        value = Builder().store_address(Address(address)).end_cell()
        dictionary.store_cell(index, value)
    return base64.b64encode(dictionary.end_dict().to_boc(False)).decode()


def getter_payload(signers=SIGNERS, proposers=None):
    proposer_item = {"type": "null"}
    if proposers:
        proposer_item = {"type": "cell", "cell": dictionary_boc(proposers)}
    return {
        "success": True,
        "exit_code": 0,
        "stack": [
            {"type": "num", "num": "0x0"},
            {"type": "num", "num": "0x2"},
            {"type": "cell", "cell": dictionary_boc(signers)},
            proposer_item,
        ],
    }


class TonMultisigVerificationTests(unittest.IsolatedAsyncioTestCase):
    def test_decodes_real_multisig_v2_getter_stack(self):
        config = decode_multisig_v2_getter(getter_payload(), TREASURY)
        self.assertEqual(config["address"], TREASURY)
        self.assertEqual(config["threshold"], 2)
        self.assertEqual(config["signers"], SIGNERS)
        self.assertEqual(config["proposers"], [])
        self.assertTrue(is_two_of_three_multisig(config, TREASURY))

    def test_accepts_only_exact_two_of_three_with_no_proposers(self):
        config = {
            "address": TREASURY,
            "threshold": 2,
            "signers": SIGNERS,
            "proposers": [],
        }
        self.assertTrue(is_two_of_three_multisig(config, TREASURY))
        self.assertFalse(is_two_of_three_multisig({**config, "threshold": 1}, TREASURY))
        self.assertFalse(is_two_of_three_multisig({**config, "threshold": 2.0}, TREASURY))
        self.assertFalse(is_two_of_three_multisig({**config, "signers": SIGNERS[:2]}, TREASURY))
        self.assertFalse(is_two_of_three_multisig(
            {**config, "signers": [TREASURY, SIGNERS[0], SIGNERS[1]]}, TREASURY,
        ))
        self.assertFalse(is_two_of_three_multisig(
            {**config, "signers": [SIGNERS[0], SIGNERS[0], SIGNERS[2]]}, TREASURY,
        ))
        self.assertFalse(is_two_of_three_multisig({**config, "address": SIGNERS[0]}, TREASURY))
        self.assertFalse(is_two_of_three_multisig({**config, "proposers": [SIGNERS[0]]}, TREASURY))
        self.assertFalse(is_two_of_three_multisig(None, TREASURY))

    def test_rejects_unexpected_or_malformed_getter_responses(self):
        with self.assertRaises(ValueError):
            decode_multisig_v2_getter({"success": False, "exit_code": 11, "stack": []}, TREASURY)
        with self.assertRaises(ValueError):
            decode_multisig_v2_getter({
                "success": True, "exit_code": 0, "stack": [
                    {"type": "num", "num": "0"},
                    {"type": "num", "num": "2"},
                    {"type": "cell", "cell": "not-base64"},
                    {"type": "null"},
                ],
            }, TREASURY)
        with self.assertRaises(ValueError):
            decode_multisig_v2_getter(getter_payload(proposers=[SIGNERS[0]]), TREASURY)

    async def test_fetches_multisig_configuration_using_contract_getter(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=getter_payload())

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://tonapi.io",
        )
        with patch.dict(os.environ, {"TONAPI_TOKEN": "test-token"}):
            config = await fetch_ton_multisig_config(TREASURY, client=client)
        await client.aclose()
        self.assertTrue(is_two_of_three_multisig(config, TREASURY))
        self.assertEqual(
            calls[0].url.path,
            f"/v2/blockchain/accounts/{TREASURY}/methods/get_multisig_data",
        )
        self.assertEqual(calls[0].headers.get("Authorization"), "Bearer test-token")

    async def test_fetches_and_validates_wallet_public_key(self):
        calls = []
        public_key = "ab" * 32

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={
                "success": True,
                "exit_code": 0,
                "stack": [{"type": "num", "num": "0x" + public_key}],
            })

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://tonapi.io",
        )
        with patch.dict(os.environ, {"TONAPI_TOKEN": "test-token"}):
            from ton_multisig import fetch_ton_wallet_public_key
            result = await fetch_ton_wallet_public_key(TREASURY, client=client)
        await client.aclose()
        self.assertEqual(result, public_key)
        self.assertEqual(
            calls[0].url.path,
            f"/v2/blockchain/accounts/{TREASURY}/methods/get_public_key",
        )
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
