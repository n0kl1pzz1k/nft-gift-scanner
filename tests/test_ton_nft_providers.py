import os
import unittest
from unittest.mock import patch

from ton_nft_providers import (
    canonical_ton_address, configured_nft_providers, configured_testnet_nft_providers,
    TonApiNftProvider, ToncenterNftProvider,
)


class TonNftProviderTests(unittest.TestCase):
    def test_friendly_and_raw_addresses_normalize_to_same_canonical_identity(self):
        friendly = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
        raw = "0:b113a994b5024a16719f69139328eb759596c38a25f59028b146fecdc3621dfe"
        self.assertEqual(canonical_ton_address(friendly), raw)
        self.assertEqual(canonical_ton_address(raw), raw)

    def test_friendly_address_checksum_is_verified(self):
        with self.assertRaises(ValueError):
            canonical_ton_address("EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDt")

    def test_valid_test_only_friendly_address_normalizes_to_raw_identity(self):
        test_only = "kQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_ntm"
        raw = "0:b113a994b5024a16719f69139328eb759596c38a25f59028b146fecdc3621dfe"
        self.assertEqual(canonical_ton_address(test_only), raw)

    def test_raw_address_rejects_invalid_length_and_workchain(self):
        with self.assertRaises(ValueError):
            canonical_ton_address("0:abcd")
        with self.assertRaises(ValueError):
            canonical_ton_address("128:" + "11" * 32)

    def test_testnet_providers_use_testnet_hosts_and_scoped_keys(self):
        with patch.dict(os.environ, {
            "TON_TESTNET_NFT_PROVIDER_IDS": "tonapi,toncenter",
            "TON_TESTNET_TONAPI_KEY": "test-testnet-tonapi-key",
            "TON_TESTNET_TONCENTER_API_KEY": "test-testnet-toncenter-key",
        }):
            providers = configured_testnet_nft_providers()
        self.assertEqual({provider.provider_id for provider in providers}, {"tonapi", "toncenter"})
        self.assertTrue(all("testnet" in provider.base_url for provider in providers))

    def test_nft_providers_reject_noncanonical_or_non_https_hosts(self):
        for factory, key in (
            (TonApiNftProvider, "api_key"),
            (ToncenterNftProvider, "api_key"),
        ):
            for url in (
                "http://testnet.tonapi.io",
                "https://testnet.tonapi.io.evil.example",
                "https://evil.example",
                "https://user:pass@testnet.tonapi.io",
            ):
                with self.subTest(factory=factory.__name__, url=url), self.assertRaises(ValueError):
                    factory(**{key: "test-key", "base_url": url})

    def test_nft_providers_accept_exact_mainnet_and_testnet_hosts(self):
        TonApiNftProvider(api_key="test-key", base_url="https://testnet.tonapi.io")
        TonApiNftProvider(api_key="test-key", base_url="https://tonapi.io")
        ToncenterNftProvider(api_key="test-key", base_url="https://testnet.toncenter.com")
        ToncenterNftProvider(api_key="test-key", base_url="https://toncenter.com")


    def test_tonapi_rejects_state_for_a_different_nft_item(self):
        import asyncio
        requested = "0:" + "11" * 32
        returned = "0:" + "22" * 32
        collection = "0:" + "33" * 32

        class Response:
            status_code = 200
            def raise_for_status(self):
                return None
            def json(self):
                return {
                    "address": returned,
                    "collection": {"address": collection},
                    "index": 7,
                    "owner": {"address": "0:" + "44" * 32},
                }

        class Client:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            async def get(self, *args, **kwargs):
                return Response()

        provider = TonApiNftProvider(api_key="test-key", base_url="https://testnet.tonapi.io")
        with patch("ton_nft_providers.httpx.AsyncClient", return_value=Client()):
            with self.assertRaisesRegex(ValueError, "different NFT item"):
                asyncio.run(provider.get_nft_state(requested))

    def test_toncenter_ignores_state_for_a_different_nft_item(self):
        import asyncio
        requested = "0:" + "11" * 32
        returned = "0:" + "22" * 32

        class Response:
            def raise_for_status(self):
                return None
            def json(self):
                return {"nft_items": [{
                    "address": returned,
                    "collection_address": "0:" + "33" * 32,
                    "index": 7,
                    "owner_address": "0:" + "44" * 32,
                    "init": True,
                }]}

        class Client:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            async def get(self, *args, **kwargs):
                return Response()

        provider = ToncenterNftProvider(api_key="test-key", base_url="https://testnet.toncenter.com")
        with patch("ton_nft_providers.httpx.AsyncClient", return_value=Client()):
            self.assertIsNone(asyncio.run(provider.get_nft_state(requested)))

    def test_provider_quorum_requires_two_named_independent_operators_and_keys(self):
        with patch.dict(os.environ, {
            "TON_NFT_PROVIDER_IDS": "tonapi,toncenter",
            "TONAPI_KEY": "test-tonapi-key",
            "TONCENTER_API_KEY": "test-toncenter-key",
        }):
            providers = configured_nft_providers()
            self.assertEqual({provider.provider_id for provider in providers}, {"tonapi", "toncenter"})
        with patch.dict(os.environ, {
            "TON_NFT_PROVIDER_IDS": "tonapi,alias",
            "TONAPI_KEY": "test-tonapi-key",
            "TONCENTER_API_KEY": "test-toncenter-key",
        }):
            with self.assertRaises(RuntimeError):
                configured_nft_providers()


if __name__ == "__main__":
    unittest.main()
