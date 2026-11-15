# Telegram compliance: Stars checkout and TON blockchain integration

**Status:** Primary-source policy summary, not a legal opinion. Kazakhstan-specific legal, tax, consumer and data-residency questions require qualified counsel.

## Official Telegram sources

1. https://telegram.org/tos/bot-developers
2. https://core.telegram.org/bots/payments-stars
3. https://core.telegram.org/bots/blockchain-guidelines
4. https://core.telegram.org/bots/api
5. https://core.telegram.org/api/stars
6. https://telegram.org/tos/mini-apps

## Digital goods and payments

Telegram's Bot Developer Terms and Payments API require digital goods and services sold inside bots and Mini Apps to be paid exclusively in Telegram Stars (XTR). A separate website or external payment provider does not remove the Stars requirement for a digital-goods sale that is still offered inside Telegram.

The bot must handle `pre_checkout_query`, wait for the authenticated `successful_payment` update, retain the Telegram charge ID, support `/paysupport` and process legitimate refunds and disputes. A pre-checkout approval is not proof of payment.

## TON blockchain integration

Telegram's blockchain rules require TON for cryptocurrency functionality in Mini Apps and TON Connect for wallet authorization and transaction signing. GIFT RADAR therefore supports TON only and uses TON Connect as its sole wallet protocol.

Permission to connect a TON wallet or verify a TON NFT does not create permission to replace Stars with a blockchain transfer for a digital-goods purchase inside Telegram. The checkout currency remains XTR.

## GIFT RADAR implementation policy

- Buyer checkout inside the Mini App: Telegram Stars (XTR) only.
- Blockchain network: TON only.
- Wallet connection and user-approved NFT transactions: TON Connect only.
- No other token, stablecoin, payment rail or blockchain network is supported.
- TON RPC/indexing providers are technical sources for NFT ownership and transaction facts, not marketplace catalogue providers.
- The exact P2P/escrow contract characterization, seller settlement model, refunds after irreversible NFT delivery, Kazakhstan legal status, taxes and consumer obligations require specialist written review before real-money release.

This document is a product-policy guardrail and source summary, not authorization to launch real payments.
