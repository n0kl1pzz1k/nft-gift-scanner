import base64
import os
import unittest
from unittest.mock import patch

import httpx

from tonsdk.boc import Builder, Cell, begin_dict
from tonsdk.utils import Address

from ton_multisig import (
    decode_multisig_v2_getter,
    decode_multisig_order_getter,
    fetch_ton_multisig_config,
    fetch_ton_multisig_orders,
    is_two_of_three_multisig,
)


TREASURY = "0:" + "a" * 64
SIGNERS = [
    "0:372b63c65f2a2d81af7d7a7d995c4fcd7bdc18e6efa50b4ee840ae9b45dc6d10",
    "0:eacaac33e932a8b67ae31e712d5a7969660130363e48e789f96c03a6162bc147",
    "0:8ede0858321e76adb84ee65790431bb800050acfc0ee5b6c1255e65137f3221c",
]


def dictionary_boc(addresses):
    dictionary = begin_dict(8)
    for index, address in enumerate(addresses):
        value = Builder().store_address(Address(address)).end_cell()
        dictionary.store_cell(index, value)
    return base64.b64encode(dictionary.end_dict().to_boc(False)).decode()


CODE_BOC_HEX = Cell().to_boc(False).hex()
DATA_BOC_HEX = Builder().store_uint(1, 1).end_cell().to_boc(False).hex()
CODE_HASH = Cell.one_from_boc(bytes.fromhex(CODE_BOC_HEX)).bytes_hash().hex()
DATA_HASH = Cell.one_from_boc(bytes.fromhex(DATA_BOC_HEX)).bytes_hash().hex()


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


def raw_account_payload():
    return {"status": "active", "code": CODE_BOC_HEX, "data": DATA_BOC_HEX}


def multisig_env():
    return {
        "HASSLE_TON_MULTISIG_EXPECTED_ADDRESS": TREASURY,
        "HASSLE_TON_MULTISIG_CODE_HASH": CODE_HASH,
        "HASSLE_TON_MULTISIG_DATA_HASH": DATA_HASH,
    }


def address_stack(address):
    cell = Builder().store_address(Address(address)).end_cell()
    return {"type": "slice", "cell": base64.b64encode(cell.to_boc(False)).decode()}


def order_actions_boc():
    from tonsdk.boc import Builder, begin_dict
    message = (
        Builder()
        .store_uint(0, 1)
        .store_bit(1).store_bit(1).store_bit(0)
        .store_address(None)
        .store_address(Address(SIGNERS[0]))
        .store_coins(0)
        .store_bit(0)
        .store_coins(0).store_coins(0)
        .store_uint(0, 64).store_uint(0, 32)
        .store_bit(0).store_bit(0)
        .end_cell()
    )
    action = Builder().store_uint(0xF1381E5B, 32).store_uint(3, 8).store_ref(message).end_cell()
    dictionary = begin_dict(8)
    dictionary.store_ref(0, action)
    return base64.b64encode(dictionary.end_dict().to_boc(False)).decode()


def order_getter_payload(seqno=0):
    return {
        "success": True,
        "exit_code": 0,
        "stack": [
            address_stack(TREASURY),
            {"type": "num", "num": str(seqno)},
            {"type": "num", "num": "2"},
            {"type": "num", "num": "0"},
            {"type": "cell", "cell": dictionary_boc(SIGNERS)},
            {"type": "num", "num": "0x3"},
            {"type": "num", "num": "2"},
            {"type": "num", "num": "2000000000"},
            {"type": "cell", "cell": order_actions_boc()},
        ],
    }


class TonMultisigVerificationTests(unittest.IsolatedAsyncioTestCase):
    def test_decodes_real_multisig_v2_getter_stack(self):
        config = decode_multisig_v2_getter(getter_payload(), TREASURY)
        self.assertEqual(config["address"], TREASURY)
        self.assertEqual(config["threshold"], 2)
        self.assertEqual(config["signers"], SIGNERS)
        self.assertEqual(config["proposers"], [])
        config.update({"code_hash": CODE_HASH, "data_hash": DATA_HASH})
        with patch.dict(os.environ, multisig_env()):
            self.assertTrue(is_two_of_three_multisig(config, TREASURY))

    def test_accepts_only_exact_two_of_three_with_no_proposers(self):
        config = {
            "address": TREASURY,
            "threshold": 2,
            "signers": SIGNERS,
            "proposers": [],
        }
        config.update({"code_hash": CODE_HASH, "data_hash": DATA_HASH})
        with patch.dict(os.environ, multisig_env()):
            self.assertTrue(is_two_of_three_multisig(config, TREASURY))
            self.assertFalse(is_two_of_three_multisig({**config, "threshold": 1}, TREASURY))
            self.assertFalse(is_two_of_three_multisig({**config, "threshold": 2.0}, TREASURY))
            self.assertFalse(is_two_of_three_multisig({**config, "signers": SIGNERS[:2]}, TREASURY))
            self.assertFalse(is_two_of_three_multisig(
                {**config, "signers": ["0:" + "b" * 64, "0:" + "c" * 64, "0:" + "d" * 64]},
                TREASURY,
            ))
            self.assertFalse(is_two_of_three_multisig(
                {**config, "signers": [TREASURY, SIGNERS[0], SIGNERS[1]]}, TREASURY,
            ))
            self.assertFalse(is_two_of_three_multisig(
                {**config, "signers": [SIGNERS[0], SIGNERS[0], SIGNERS[2]]}, TREASURY,
            ))
            self.assertFalse(is_two_of_three_multisig({**config, "address": SIGNERS[0]}, TREASURY))
            self.assertFalse(is_two_of_three_multisig({**config, "proposers": [SIGNERS[0]]}, TREASURY))
            self.assertFalse(is_two_of_three_multisig({**config, "code_hash": "00"}, TREASURY))
            self.assertFalse(is_two_of_three_multisig({**config, "data_hash": "00"}, TREASURY))
            self.assertFalse(is_two_of_three_multisig(None, TREASURY))
        self.assertFalse(is_two_of_three_multisig(config, TREASURY))

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
        with patch.dict(os.environ, multisig_env()):
            config = decode_multisig_v2_getter(getter_payload(proposers=[SIGNERS[0]]), TREASURY)
            config.update({"code_hash": CODE_HASH, "data_hash": DATA_HASH})
            self.assertFalse(is_two_of_three_multisig(config, TREASURY))

    def test_decodes_send_message_action_and_keeps_approval_blocked(self):
        from tonsdk.boc import Builder, begin_dict
        recipient = "0:" + "c" * 64
        body = Builder().store_uint(0x5FCC3D14, 32).end_cell()
        message = (
            Builder()
            .store_uint(0, 1)  # internal message
            .store_bit(1).store_bit(1).store_bit(0)
            .store_address(None)
            .store_address(Address(recipient))
            .store_coins(1234567890)
            .store_bit(0)  # no extra currencies
            .store_coins(0).store_coins(0)
            .store_uint(0, 64).store_uint(0, 32)
            .store_bit(0)  # no StateInit
            .store_bit(1).store_ref(body)
            .end_cell()
        )
        action = Builder().store_uint(0xF1381E5B, 32).store_uint(3, 8).store_ref(message).end_cell()
        dictionary = begin_dict(8)
        dictionary.store_ref(0, action)
        dictionary.store_ref(1, action)
        boc = base64.b64encode(dictionary.end_dict().to_boc(False)).decode()
        from ton_multisig import decode_multisig_order_actions
        decoded = decode_multisig_order_actions(boc)
        self.assertEqual(decoded["action_count"], 2)
        self.assertFalse(decoded["approval_safe"])
        self.assertEqual(decoded["actions"][0]["recipient"], recipient)
        self.assertEqual(decoded["actions"][0]["amount_nano"], "1234567890")
        self.assertEqual(decoded["actions"][0]["body_op"], 0x5FCC3D14)
        self.assertEqual(decoded["actions"][0]["body_kind"], "nft_transfer")
        self.assertEqual(decoded["actions"][1]["index"], 1)

    def test_unknown_action_is_never_approval_safe(self):
        from tonsdk.boc import Builder, begin_dict
        action = Builder().store_uint(0xDEADBEEF, 32).end_cell()
        dictionary = begin_dict(8)
        dictionary.store_ref(0, action)
        boc = base64.b64encode(dictionary.end_dict().to_boc(False)).decode()
        from ton_multisig import decode_multisig_order_actions
        decoded = decode_multisig_order_actions(boc)
        self.assertEqual(decoded["actions"][0]["kind"], "unknown")
        self.assertFalse(decoded["approval_safe"])

    def test_decodes_order_getter_metadata(self):
        from ton_multisig import decode_multisig_order_getter
        order = decode_multisig_order_getter(order_getter_payload(7))
        self.assertEqual(order["multisig"], TREASURY)
        self.assertEqual(order["order_seqno"], 7)
        self.assertEqual(order["threshold"], 2)
        self.assertEqual(order["approvals_num"], 2)
        self.assertEqual(order["approvals_mask"], "3")
        self.assertEqual(order["expiration_date"], 2000000000)
        self.assertTrue(order["has_order_payload"])
        self.assertRegex(order["order_payload_hash"], r"^[0-9a-f]{64}$")
        self.assertFalse(order["sent_for_execution"])
        self.assertEqual(order["approvals"], [
            {"address": SIGNERS[0], "approved": True},
            {"address": SIGNERS[1], "approved": True},
            {"address": SIGNERS[2], "approved": False},
        ])

    def test_rejects_order_approval_count_that_disagrees_with_mask(self):
        payload = order_getter_payload(8)
        payload["stack"][6]["num"] = "1"
        with self.assertRaises(ValueError):
            decode_multisig_order_getter(payload)

    def test_order_getter_rejects_malformed_stack(self):
        with self.assertRaises(ValueError):
            decode_multisig_order_getter({"success": True, "exit_code": 0, "stack": []})

    async def test_lists_recent_orders_from_official_getters(self):
        calls = []
        order_address = "0:" + "b" * 64
        def handler(request):
            calls.append(request)
            if request.url.path.endswith("/get_order_address"):
                return httpx.Response(200, json={
                    "success": True, "exit_code": 0,
                    "stack": [address_stack(order_address)],
                })
            return httpx.Response(200, json=order_getter_payload(4))
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://tonapi.io",
        )
        with patch.dict(os.environ, {"TONAPI_TOKEN": "test-token"}):
            orders = await fetch_ton_multisig_orders(TREASURY, 5, limit=1, client=client)
        await client.aclose()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["address"], order_address)
        self.assertEqual(orders[0]["order_seqno"], 4)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].url.path, f"/v2/blockchain/accounts/{TREASURY}/methods/get_order_address")
        self.assertEqual(calls[0].read().decode(), '{"args":[{"type":"tinyint","value":"4"}]}')
        self.assertEqual(calls[1].url.path, f"/v2/blockchain/accounts/{order_address}/methods/get_order_data")

    async def test_fetches_multisig_configuration_using_contract_getter(self):
        calls = []

        def handler(request):
            calls.append(request)
            if request.url.path.endswith("/methods/get_multisig_data"):
                return httpx.Response(200, json=getter_payload())
            return httpx.Response(200, json=raw_account_payload())

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://tonapi.io",
        )
        with patch.dict(os.environ, {"TONAPI_TOKEN": "test-token", **multisig_env()}):
            config = await fetch_ton_multisig_config(TREASURY, client=client)
            self.assertEqual(config["code_hash"], CODE_HASH)
            self.assertEqual(config["data_hash"], DATA_HASH)
            self.assertTrue(is_two_of_three_multisig(config, TREASURY))
        await client.aclose()
        self.assertEqual(
            calls[0].url.path,
            f"/v2/blockchain/accounts/{TREASURY}/methods/get_multisig_data",
        )
        self.assertEqual(
            calls[1].url.path,
            f"/v2/blockchain/accounts/{TREASURY}",
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
