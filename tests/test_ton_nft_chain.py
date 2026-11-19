import base64
import unittest
from unittest.mock import patch

import httpx

from ton_nft_chain import (
    _stack_address,
    _stack_number,
    _wait_for_toncenter_public_rate_limit,
    verify_nft_collection_membership,
    verify_nft_owner_onchain,
)


ITEM = "0:" + "a" * 64
COLLECTION = "0:" + "b" * 64
OWNER = "0:" + "c" * 64


class TonNftChainVerificationTests(unittest.IsolatedAsyncioTestCase):
    def test_stack_number_accepts_decimal_and_hex(self):
        self.assertEqual(_stack_number(["num", "17"]), 17)
        self.assertEqual(_stack_number(["num", "0x11"]), 17)

    def test_stack_number_rejects_wrong_types(self):
        with self.assertRaises(ValueError):
            _stack_number(["slice", "not-a-number"])

    def test_stack_address_decodes_standard_internal_address(self):
        class FakeSlice:
            def read_uint(self, bits):
                return 2

            def read_bit(self):
                return 0

            def read_int(self, bits):
                return 0

            def read_bytes(self, size):
                return bytes.fromhex("a" * 64)

        class FakeCell:
            def begin_parse(self):
                return FakeSlice()

        with patch("tonsdk.boc.Cell.one_from_boc", return_value=FakeCell()):
            encoded = base64.b64encode(b"test-boc").decode("ascii")
            self.assertEqual(_stack_address(["slice", encoded]), ITEM)

    async def test_verifies_owner_collection_and_collection_derived_item_address(self):
        calls = []

        def handler(request):
            calls.append(request)
            if request.url.path.endswith("/runGetMethod"):
                payload = __import__("json").loads(request.content)
                if payload["method"] == "get_nft_data":
                    return httpx.Response(200, json={
                        "ok": True,
                        "result": {"exit_code": 0, "block_id": {"seqno": 12345}, "stack": [
                            ["num", "1"], ["num", "42"], ["slice", "item-collection"],
                            ["slice", "item-owner"], ["cell", "ignored"],
                        ]},
                    })
                return httpx.Response(200, json={
                    "ok": True,
                    "result": {"exit_code": 0, "block_id": {"seqno": 12345}, "stack": [["slice", "derived-item"]]},
                })
            return httpx.Response(404)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://toncenter.com")
        with patch.dict("os.environ", {"TONCENTER_API_KEY": "test-token"}):
            with patch("ton_nft_chain._stack_address", side_effect=[COLLECTION, OWNER, ITEM]):
                result = await verify_nft_collection_membership(ITEM, COLLECTION, OWNER, client=client)
        await client.aclose()

        self.assertTrue(result["verified"])
        self.assertEqual(result["index"], 42)
        self.assertEqual(result["block_seqno"], 12345)
        requests = [__import__("json").loads(call.content) for call in calls]
        self.assertEqual([request["method"] for request in requests], [
            "get_nft_data", "get_nft_address_by_index"
        ])
        self.assertEqual(requests[1]["seqno"], 12345)

    async def test_rejects_item_when_live_owner_does_not_match(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
                "ok": True,
                "result": {"exit_code": 0, "block_id": {"seqno": 12345}, "stack": [
                    ["num", "1"], ["num", "42"], ["slice", "collection"],
                    ["slice", "wrong-owner"], ["cell", "ignored"],
                ]},
            })),
            base_url="https://toncenter.com/api/v2",
        )
        with patch.dict("os.environ", {"TONCENTER_API_KEY": "test-token"}):
            with patch("ton_nft_chain._stack_address", side_effect=[COLLECTION, "0:" + "d" * 64]):
                result = await verify_nft_collection_membership(ITEM, COLLECTION, OWNER, client=client)
        await client.aclose()
        self.assertFalse(result["verified"])

    async def test_tonapi_get_method_verifies_live_owner_with_bearer_token(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={
                "success": True,
                "exit_code": 0,
                "decoded": {
                    "init": True,
                    "index": "42",
                    "collection_address": COLLECTION,
                    "owner_address": OWNER,
                },
            })

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://tonapi.io",
        )
        with patch.dict("os.environ", {"TONAPI_TOKEN": "test-tonapi-token"}):
            result = await verify_nft_owner_onchain(ITEM, [OWNER], client=client)
        await client.aclose()
        self.assertTrue(result["verified"])
        self.assertEqual(result["owner_address"], OWNER)
        self.assertEqual(result["collection_address"], COLLECTION)
        self.assertEqual(result["index"], 42)
        self.assertEqual(requests[0].headers["Authorization"], "Bearer test-tonapi-token")

    async def test_tonapi_get_method_rejects_stale_indexer_owner(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
                "success": True,
                "exit_code": 0,
                "decoded": {
                    "init": True,
                    "index": "42",
                    "collection_address": COLLECTION,
                    "owner_address": "0:" + "d" * 64,
                },
            })),
            base_url="https://tonapi.io",
        )
        with patch.dict("os.environ", {"TONAPI_TOKEN": "test-tonapi-token"}):
            result = await verify_nft_owner_onchain(ITEM, [OWNER], client=client)
        await client.aclose()
        self.assertFalse(result["verified"])

    async def test_toncenter_fallback_verifies_owner_when_tonapi_token_is_absent(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
                "ok": True,
                "result": {
                    "exit_code": 0,
                    "block_id": {"seqno": 12345},
                    "stack": [
                        ["num", "1"], ["num", "42"],
                        ["slice", "collection"], ["slice", "owner"], ["cell", "ignored"],
                    ],
                },
            })),
            base_url="https://toncenter.com",
        )
        with patch.dict("os.environ", {"TONCENTER_API_KEY": "test-toncenter-token"}, clear=True):
            with patch("ton_nft_chain._stack_address", side_effect=[COLLECTION, OWNER]):
                result = await verify_nft_owner_onchain(ITEM, OWNER, client=client)
        await client.aclose()
        self.assertTrue(result["verified"])
        self.assertEqual(result["provider"], "toncenter_get_method")
        self.assertEqual(result["block_seqno"], 12345)

    async def test_public_toncenter_requests_are_spaced_at_least_one_second(self):
        clock = [10.0]
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)
            clock[0] += delay

        with patch.dict("os.environ", {}, clear=True), \
             patch("ton_nft_chain._TONCENTER_LAST_REQUEST_AT", 0.0), \
             patch("ton_nft_chain.time.monotonic", side_effect=lambda: clock[0]), \
             patch("ton_nft_chain.asyncio.sleep", new=fake_sleep):
            await _wait_for_toncenter_public_rate_limit()
            await _wait_for_toncenter_public_rate_limit()

        self.assertEqual(len(sleeps), 1)
        self.assertGreaterEqual(sleeps[0], 1.0)

    async def test_fails_closed_when_contract_getter_fails(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(503)),
            base_url="https://toncenter.com/api/v2",
        )
        with patch.dict("os.environ", {"TONCENTER_API_KEY": "test-token"}):
            with self.assertRaises(httpx.HTTPStatusError):
                await verify_nft_collection_membership(ITEM, COLLECTION, OWNER, client=client)
        await client.aclose()


if __name__ == "__main__":
    unittest.main()
