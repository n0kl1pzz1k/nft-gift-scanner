# GIFT RADAR

**GIFT RADAR is an independent Telegram-gift marketplace.** It is not a Portals, Tonnel, MRKT or third-party marketplace frontend, and it does not aggregate their listings.

## Product boundary

- Users create and manage listings inside GIFT RADAR.
- Search, collections, offers, orders, seller reputation, notifications and deal lifecycle belong to GIFT RADAR.
- Third-party marketplace APIs, listing feeds, aggregators, cross-market matching and external-market price ingestion are disabled.
- TON RPC/indexing services may be used only as technical infrastructure to verify chain facts such as NFT ownership. They are not sources of marketplace listings.
- A wallet connection proves control of a wallet, not ownership of a specific gift.
- No wallet seed phrase, private key, exchange API key or wallet password is requested or stored.

## Current implementation status

The repository contains Telegram authentication/session infrastructure, user profiles, internal demo order/offer flows, moderation/reporting, PostgreSQL migration foundations, append-only financial ledger foundations, a TON Proof service foundation, and Telegram Stars payment foundations behind fail-closed gates.

The native inventory migration adds canonical asset records, immutable ownership evidence, native listings, reservation leases and append-only listing events. The Telegram Stars foundation now includes terms acceptance, idempotent invoice creation, pre-checkout validation, authenticated successful-payment settlement, buyer refund requests, an admin-claimed Telegram refund call and a compensating ledger reversal. The production ownership verifier, public listing publication, reservation-to-real-deal transition, delivery confirmation, disputes, full refund reconciliation and seller settlement remain release-gated. Existing demo orders are not real trades and must never be charged.

The catalogue endpoints currently return an honest empty native catalogue while the native inventory query and trusted ownership verification are completed. They do not fall back to external marketplaces or invent listings.

## Payments and custody

- Payment execution is behind the pluggable `PaymentRoute` interface. The deal core selects a route through configuration and does not contain provider-specific payment logic.
- `ton_usdt_testnet` is the enabled engineering route for TON testnet and the platform-issued `tUSDT` test Jetton only. It is not genuine Tether USDT and cannot select mainnet.
- `stars` is a separate adapter and remains hard-disabled. No Stars checkout or payout is activated by selecting a config value.
- **Legal status:** the payment route for sales inside the Telegram Mini App has not been determined. The open questions for counsel are collected in [docs/TELEGRAM_COMPLIANCE.md](docs/TELEGRAM_COMPLIANCE.md). This code-level testnet route is not authorization to accept crypto payments inside a Mini App.
- Mainnet, real funds and production payment activation remain prohibited until separate legal, security and owner approvals.
- GIFT RADAR does not request wallet seed phrases/private keys or store user wallet credentials. Transaction signing must remain in an explicitly user-approved wallet or external signing system.
- A seller listing must be backed by fresh, independently verifiable ownership evidence and a reservation lock before a real deal can be created.

## Security and operations

- Telegram Mini App initData is signature- and freshness-checked before exchanging it for a short-lived opaque session.
- Session tokens are stored hashed and can be revoked.
- Telegram webhook requests require the configured secret header and use a durable idempotent update inbox.
- Financial records use append-only double-entry ledger foundations; no mutable balance-editing interface exists.
- PostgreSQL migration/cutover and production deployment are separate controlled release steps. CI success does not mean production is deployed.
- Keep real-deal and Stars feature flags disabled until all release gates pass.

## Local development

The frontend is in frontend/ and uses Svelte 5 + Vite.

    cd frontend
    npm install
    npm run check
    npm run build

Backend dependencies are listed in requirements.txt. The API is FastAPI; use a local environment and never copy production secrets into source control.

## Retired integrations

External marketplace adapters and public reference-feed routes have been removed from the active backend. GIFT RADAR owns its own catalogue, listings, orders and deal lifecycle. TON RPC/indexers may be used only for technical chain-state verification, not as sources of third-party listings. No Portals, Swift Gifts, Tonnel or MRKT credentials are required or should be configured.
