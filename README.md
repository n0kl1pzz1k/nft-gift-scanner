# GIFT RADAR

**GIFT RADAR** is an independent Telegram NFT-gift marketplace. It owns its catalogue, listings, offers, orders and deal lifecycle. It is not a frontend or catalogue mirror for Portals, Tonnel, MRKT or another marketplace.

## Product and technology boundary

- The marketplace catalogue and listing records belong to GIFT RADAR.
- External marketplace APIs, listing feeds, aggregators and cross-market price ingestion are not part of the product.
- Telegram Stars (XTR) is the only purchase currency inside the Telegram Mini App.
- TON is the only supported blockchain network. TON Connect is the only wallet connection protocol.
- TON wallet addresses and chain evidence are used for wallet proof and NFT ownership/transfer verification. Wallet connection alone is never treated as proof of NFT ownership.
- No seed phrase, private key, wallet password or exchange API key is requested or stored.

## Implemented foundations

The repository includes Telegram Mini App authentication and session infrastructure, user profiles, bot/webhook processing, a Svelte 5 + Vite frontend, catalogue and collection interfaces, internal demo order/offer flows, moderation/reporting foundations, PostgreSQL/Alembic migration foundations, append-only double-entry ledger foundations, Telegram Stars invoice/refund handling, TON Connect integration, server-side TON Proof verification, dual-provider TON NFT state verification, seller-owned inventory records, private listing drafts, reservation/deal lifecycle foundations, dispute/reconciliation records, and a TON NFT escrow contract prototype with local tests.

The Stars backend validates invoice payload, buyer, XTR currency and amount; accepts payment only from Telegram's authenticated successful-payment update; records the unique Telegram charge ID; and includes refund request, admin execution and reconciliation handling.

## Payments and wallets

- **Checkout:** Telegram Stars (XTR) only.
- **Blockchain:** TON only.
- **Wallet protocol:** TON Connect only.
- **Wallets:** TON Connect-compatible wallets such as Telegram Wallet, Tonkeeper and MyTonWallet.
- **TON is used for blockchain operations**, including wallet proof, NFT ownership and user-approved NFT transactions. It is not an alternative in-Mini-App checkout for digital goods.
- No other token, stablecoin or blockchain payment route is supported.

## Security foundations

- Telegram initData is signature- and freshness-checked before it is exchanged for a short-lived opaque session.
- Session tokens are stored hashed and can be revoked.
- Telegram webhook requests require the configured secret header and use a durable idempotent update inbox.
- Financial records use append-only double-entry ledger foundations. There is no user-facing mutable balance setter.
- TON Proof is verified server-side. A connected wallet is not treated as proof of a specific NFT.
- Production database cutover and deployment are controlled release steps. CI success does not mean production is deployed.

## Development

Frontend:
```bash
cd frontend
npm install
npm run check
npm run build
```

Backend dependencies are listed in `requirements.txt`. Never place production secrets or wallet credentials in source control or chat.
