import base64
import unittest
from unittest.mock import patch

import httpx

from ton_nft_chain import _stack_address, _stack_number, verify_nft_collection_membership


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
                        "result": {"exit_code": 0, "stack": [
                            ["num", "1"], ["num", "42"], ["slice", "item-collection"],
                            ["slice", "item-owner"], ["cell", "ignored"],
                        ]},
                    })
                return httpx.Response(200, json={
                    "ok": True,
                    "result": {"exit_code": 0, "stack": [["slice", "derived-item"]]},
                })
            return httpx.Response(404)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://toncenter.com")
        with patch.dict("os.environ", {"TONCENTER_API_KEY": "test-token"}):
            with patch("ton_nft_chain._stack_address", side_effect=[COLLECTION, OWNER, ITEM]):
                result = await verify_nft_collection_membership(ITEM, COLLECTION, OWNER, client=client)
        await client.aclose()

        self.assertTrue(result["verified"])
        self.assertEqual(result["index"], 42)
        self.assertEqual([__import__("json").loads(c.content)["method"] for c in calls], [
            "get_nft_data", "get_nft_address_by_index"
        ])

    async def test_rejects_item_when_live_owner_does_not_match(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
                "ok": True,
                "result": {"exit_code": 0, "stack": [
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

    async def test_fails_closed_when_contract_getter_fails(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(503)),
            base_url="https://toncenter.com/api/v2",
        )
        with self.assertRaises(httpx.HTTPStatusError):
            await verify_nft_collection_membership(ITEM, COLLECTION, OWNER, client=client)
        await client.aclose()


if __name__ == "__main__":
    unittest.main()
