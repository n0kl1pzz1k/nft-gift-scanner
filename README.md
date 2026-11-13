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

The native inventory migration adds canonical asset records, immutable ownership evidence, native listings, reservation leases and append-only listing events. The production-grade ownership verifier, public listing publication path, reservation-to-real-deal transition, delivery confirmation, disputes, refunds and seller settlement are not yet complete. Existing demo orders are not real trades and must never be charged.

The catalogue endpoints currently return an honest empty native catalogue while the native inventory query and trusted ownership verification are completed. They do not fall back to external marketplaces or invent listings.

## Payments and custody

- Digital goods sold inside Telegram must use Telegram Stars (XTR), subject to Telegram's current rules.
- Stars payments remain disabled until real listing/deal creation, terms, fulfilment, refund handling, reconciliation and security review are complete.
- Crypto checkout and automated crypto transfers remain disabled.
- GIFT RADAR does not custody user wallets, sign transactions, edit user balances or take control of user assets.
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

Legacy Portals adapter code is retained temporarily only to make the removal auditable and prevent accidental API calls. Requests through that adapter fail closed with HTTP 410. No Portals, Swift Gifts, Tonnel or MRKT credentials are required or should be configured. The legacy adapter can be physically deleted after all old deployment references are migrated.
