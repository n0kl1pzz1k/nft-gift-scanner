"""Immutable release safety policy for the current MVP workstream.

This repository is currently in a testnet-only engineering phase. These flags
are code constants, not environment variables: setting legacy Railway feature
flags must never activate a mainnet payment, payout, or NFT custody path.

A constant may be changed only in a reviewed PR after the corresponding
testnet adapter and end-to-end test suite have been implemented.
"""

TESTNET_ONLY = True

# The current TON deposit and payout adapters are mainnet-specific. A testnet
# adapter must replace them before either capability can become available.
TON_TESTNET_DEPOSIT_ADAPTER_READY = False
TON_TESTNET_WITHDRAWAL_ADAPTER_READY = False

# Current NFT ownership providers and inventory schema are mainnet-specific.
TON_TESTNET_NFT_VERIFIER_READY = False

# Telegram Stars is connected to the configured production Bot API. No isolated
# Telegram test-server integration exists yet, so it is not a test payment rail.
TELEGRAM_STARS_TEST_ENV_READY = False
