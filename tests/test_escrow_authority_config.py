from __future__ import annotations

import pytest

from escrow_authority_config import build_testnet_escrow_snapshot, load_platform_escrow_authorities


def address(char: str) -> str:
    return "0:" + char * 64


def env_config() -> dict[str, str]:
    return {
        "GIFT_RADAR_ESCROW_NETWORK": "ton-testnet",
        "GIFT_RADAR_ESCROW_PLATFORM_CONFIG_AUTHORITY": address("1"),
        "GIFT_RADAR_ESCROW_SIGNER_1": address("2"),
        "GIFT_RADAR_ESCROW_SIGNER_2": address("3"),
        "GIFT_RADAR_ESCROW_SIGNER_3": address("4"),
        "GIFT_RADAR_ESCROW_ARBITRATOR_1": address("5"),
        "GIFT_RADAR_ESCROW_ARBITRATOR_2": address("6"),
        "GIFT_RADAR_ESCROW_ARBITRATOR_3": address("7"),
        "GIFT_RADAR_ESCROW_RESOLVER_MULTISIG": address("8"),
        "GIFT_RADAR_ESCROW_RESOLVER_CODE_HASH": "a" * 64,
        "GIFT_RADAR_TEST_JETTON_MASTER": address("9"),
        "GIFT_RADAR_ESCROW_AUTHORITY_VERSION": "test-v1",
    }


def test_platform_authority_config_loads_fixed_two_of_three_sets():
    config = load_platform_escrow_authorities(env_config())
    assert config.network == "ton-testnet"
    assert config.signers == (address("2"), address("3"), address("4"))
    assert config.signer_threshold == 2
    assert config.arbitrators == (address("5"), address("6"), address("7"))
    assert config.arbitrator_threshold == 2
    assert config.resolver_multisig == address("8")


@pytest.mark.parametrize("field", [
    "signers", "signer1", "arbitrators", "resolver_multisig",
    "platform_config_authority", "authority_config",
])
def test_client_cannot_inject_or_override_platform_authorities(field: str):
    with pytest.raises(ValueError, match="Client payload cannot set platform authority fields"):
        build_testnet_escrow_snapshot(
            seller_address=address("a"),
            buyer_address=address("b"),
            nft_item_address=address("c"),
            asset_kind="TON",
            amount_raw=100,
            client_payload={field: [address("d")]},
            environ=env_config(),
        )


def test_seller_and_buyer_cannot_be_authority_members():
    with pytest.raises(ValueError, match="Seller cannot be a platform authority"):
        build_testnet_escrow_snapshot(
            seller_address=address("2"),
            buyer_address=address("b"),
            nft_item_address=address("c"),
            asset_kind="TON",
            amount_raw=100,
            environ=env_config(),
        )
    with pytest.raises(ValueError, match="Buyer cannot be a platform authority"):
        build_testnet_escrow_snapshot(
            seller_address=address("a"),
            buyer_address=address("5"),
            nft_item_address=address("c"),
            asset_kind="TON",
            amount_raw=100,
            environ=env_config(),
        )


def test_duplicate_or_overlapping_platform_authorities_are_rejected():
    env = env_config()
    env["GIFT_RADAR_ESCROW_ARBITRATOR_2"] = env["GIFT_RADAR_ESCROW_SIGNER_1"]
    with pytest.raises(ValueError, match="must all be distinct"):
        load_platform_escrow_authorities(env)


def test_testnet_only_and_resolver_code_hash_are_mandatory():
    env = env_config()
    env["GIFT_RADAR_ESCROW_NETWORK"] = "ton-mainnet"
    with pytest.raises(ValueError, match="ton-testnet"):
        load_platform_escrow_authorities(env)
    env = env_config()
    env["GIFT_RADAR_ESCROW_RESOLVER_CODE_HASH"] = "not-a-hash"
    with pytest.raises(ValueError, match="pinned resolver multisig code hash"):
        load_platform_escrow_authorities(env)


def test_snapshot_hash_is_deterministic_and_uses_platform_config():
    env = env_config()
    first = build_testnet_escrow_snapshot(
        seller_address=address("a"),
        buyer_address=address("b"),
        nft_item_address=address("c"),
        asset_kind="TEST_USDT_JETTON",
        amount_raw=1000000,
        environ=env,
    )
    second = build_testnet_escrow_snapshot(
        seller_address=address("a"),
        buyer_address=address("b"),
        nft_item_address=address("c"),
        asset_kind="TEST_USDT_JETTON",
        amount_raw=1000000,
        environ=env,
    )
    assert first["configuration_hash"] == second["configuration_hash"]
    assert first["signers"] == [address("2"), address("3"), address("4")]
    assert first["test_jetton_master"] == address("9")
    assert first["network"] == "ton-testnet"


def test_test_jetton_requires_platform_master_address():
    env = env_config()
    env.pop("GIFT_RADAR_TEST_JETTON_MASTER")
    with pytest.raises(ValueError, match="test Jetton master is not configured"):
        build_testnet_escrow_snapshot(
            seller_address=address("a"),
            buyer_address=address("b"),
            nft_item_address=address("c"),
            asset_kind="TEST_USDT_JETTON",
            amount_raw=1000000,
            environ=env,
        )
