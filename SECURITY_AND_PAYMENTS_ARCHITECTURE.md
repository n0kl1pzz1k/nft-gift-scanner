# GIFT RADAR — Security and Payments Architecture

Status: architecture decision record, not a live payment implementation.
Branch: security/payment-architecture
Date: 2026-11-13

## 1. Product boundary

GIFT RADAR is intended to become an independently operated marketplace with its own accounts, listings, orders, offers, deal lifecycle, payment records, moderation and audit trail.

Third-party marketplace APIs (Portals, Getgems, MarketApp, Tonnel/MRKT aggregators) must not be a source of truth for GIFT RADAR orders, balances, ownership, payment state or settlement. Any optional external feed is discovery/reference-only and can be disabled without disabling the native marketplace.

Removing APIs from code means removing secrets, credentials and private provider access from source control and browser code. The application still needs its own authenticated backend API. Public service URLs, public chain contract addresses and public wallet addresses are not secrets.

## 2. Non-negotiable security rules

- No seed phrases, private keys, wallet passwords, exchange credentials or user API keys are collected or stored.
- No private API key, bot token, webhook secret, database URL/password, signing key or encryption key may appear in Git, frontend bundles, logs, screenshots, chat or error responses.
- Runtime secrets live only in Railway Variables or a dedicated secret manager. Rotate any credential ever committed or exposed; deleting it from the latest source is not sufficient.
- Frontend requests go only to the GIFT RADAR backend. The backend owns authorization, validation, pricing, order state and payment state.
- Wallet connection is address/signature based. The user signs in their wallet; GIFT RADAR never receives signing material.
- Admin and owner roles have no endpoint or database procedure that directly edits user balances, payment confirmations, transaction IDs, seller proceeds or ledger entries.
- No manual credit/debit balance feature, no hidden balance override, and no admin impersonation for financial actions.
- Financial records are append-only. Corrections are compensating entries with a reason, actor, evidence and immutable audit event, never UPDATE/DELETE of historical ledger rows.
- All state-changing operations require authorization, strict input validation, idempotency keys, rate limits and database transactions.
- No payment is considered complete based on a client callback, screenshot, submitted transaction hash alone, unconfirmed chain event, invoice creation, or pre-checkout approval.
- Payment adapters are allowlisted by exact method and provider. No arbitrary URL, arbitrary token contract, or arbitrary chain is accepted from the client.

## 3. Required production architecture

1. Telegram Mini App / web frontend: presentation only; no secrets and no financial authority.
2. API gateway: TLS, strict CORS allowlist, request-size limits, per-IP and per-account rate limits, security headers, abuse controls.
3. Identity service: verified Telegram initData exchanged for short-lived opaque sessions; session digests stored server-side; rotation, revocation and replay protection.
4. Marketplace service: users, profiles, collections, listings, offers, orders, deal state machine, disputes and moderation.
5. Payment orchestration service: creates payment intents from server-owned order totals; verifies provider/chain evidence; idempotently records settlement.
6. Ledger service: append-only double-entry journal, immutable entries, reconciliation and separate pending/available/held liabilities.
7. Chain watchers: read-only observers for TON and TRON deposits; exact allowlisted token contracts, destination addresses, decimals, transaction status and finality.
8. PostgreSQL: production system of record, constraints, foreign keys, transaction isolation, backups and point-in-time recovery. SQLite is development/single-instance only and must not be used as the production financial ledger.
9. Background workers and transactional outbox: durable retries, deduplication and reconciliation without duplicate fulfilment.
10. Audit/security monitoring: append-only security events, alerting, privileged-action review and incident response.

## 4. Ledger and balance invariants

- A displayed balance is a derived projection of posted ledger entries, not a mutable user field.
- Each journal transaction balances: total debits equal total credits for each asset/currency.
- Currency/network are separate asset identifiers: XTR, USDT-TRC20, USDT-TON and GRAM-on-TON are never interchangeable ticker strings.
- Pending deposits, confirmed deposits, available funds, funds reserved for orders, seller proceeds, fees, refunds and chargebacks are separate accounts/states.
- A chain deposit is credited only after independent server-side verification of the exact token contract, destination, raw integer amount/decimals, successful execution and configured finality threshold.
- Duplicate chain events are rejected by a unique key such as (network, transaction hash, event/log index, token contract).
- Reorgs, delayed indexers, failed smart-contract execution, underpayment, overpayment, wrong token, wrong network and expired payment intents have explicit states and reconciliation procedures.
- No owner/admin UI can write journal entries. Exceptional adjustments require a separately governed, multi-party approval process and are always visible in the audit trail.

## 5. Payment rail constraints

### Telegram Stars (XTR)

For digital goods/services sold inside Telegram bots or Mini Apps, Telegram's official Payments documentation requires Telegram Stars (XTR) exclusively. Crypto or third-party payment providers must not be offered as an in-app checkout alternative for those digital goods/services.

Implementation requirements when this rail is approved:
- Create invoice server-side from an existing order and immutable server-owned price.
- Use currency XTR; never accept client-supplied amount/currency as authoritative.
- Validate pre_checkout_query against user, order, payload, amount, currency, expiry and current order state.
- A successful pre_checkout_query is not payment. Fulfil only after authenticated Telegram successful_payment.
- Persist telegram_payment_charge_id and provider charge references with unique constraints.
- Make webhook processing idempotent; handle refunds via the documented Stars refund flow.
- Provide /terms, /support and /paysupport, dispute handling, clear purchase terms, and durable backups.
- Test in Telegram's payment test environment before production.
- Confirm that the marketplace's specific NFT/listing transaction model is permitted under Telegram terms before enabling Stars checkout.

Official reference: https://core.telegram.org/bots/payments-stars

### Crypto rails: USDT-TRC20, USDT-TON, GRAM on TON

These are NOT to be exposed as payment options inside the Telegram Mini App for digital goods/services unless Telegram's current rules expressly permit the exact use case. No external web checkout is presumed permissible for this marketplace flow. Keep crypto checkout disabled unless qualified counsel and Telegram/platform review confirm in writing that the exact transaction model is allowed. Do not use an external link, webview, or alternate interface to circumvent Telegram policy.

- TON wallet connection: use TON Connect. Wallet keys remain in the user's wallet; backend verifies signed messages and on-chain transactions independently.
- TRON deposits: watch only the exact allowlisted USDT TRC-20 contract and merchant destination. Confirm the solidified transaction receipt and successful contract execution; validate the canonical Transfer event emitted by the allowlisted token contract, sender, recipient and raw amount.
- TON deposits: verify the exact USDT jetton master contract, wallet/jetton transfer event, recipient, amount and transaction finality using a trusted TON node/indexer plus independent reconciliation.
- GRAM: do not enable based on ticker text. Customer must supply and approve the exact canonical TON master contract address, decimals, network and custody/settlement model. Verify independently before allowlisting.
- No transaction signing or broadcasting from the backend. No user private keys. Deposit monitoring is read-only.
- Do not treat a broadcast response or a transaction hash supplied by the browser as proof of payment.
- Payment addresses are server-issued and bound to one payment intent. Avoid shared deposit addresses without reliable per-payment attribution.
- A payment intent has an expiry, exact asset/network, exact amount, destination, order ID and unique reference. Late/partial/overpaid deposits enter manual reconciliation, not automatic fulfilment.

Official references:
- TON Connect: https://docs.ton.org/applications/ton-connect/overview
- TRON confirmation semantics: https://developers.tron.network/docs/confirmation-semantics
- TRON solidified receipt: https://developers.tron.network/reference/gettransactioninfobyid-1
- TRON event logs: https://developers.tron.network/docs/event

## 6. Existing repository findings (verified from current main branch)

- Repository: n0kl1pzz1k/nft-gift-scanner, private, main branch.
- Current code is GIFT RADAR v0.4.0, a FastAPI + aiogram Telegram bot and Mini App.
- Existing Telegram initData HMAC verification, freshness bounds, replay protection, opaque hashed sessions, session revocation, webhook secret validation, request-size limit and durable Telegram update inbox are useful foundations.
- Existing PORTALS_API_KEY and SWIFTGIFTS_API_KEY are read from server environment variables; do not move them into frontend code. These integrations are optional catalogue feeds and are not a native marketplace or payment system.
- config.js contains a public backend base URL. A public API base URL is not a secret; it can later be replaced with a same-origin deployment path for simpler CORS.
- marketplace.py and demo_offers.py currently describe simulation-only order/offer/deal state. Their amounts are not real balances and their deal state is not settlement.
- The current database is SQLite with WAL. It is not suitable as the multi-instance production ledger. Migrate marketplace/account/order/payment state to PostgreSQL before accepting real funds.
- railway.json currently starts only uvicorn app:app; the repository README describes a webhook/bot architecture, so deployment startup and bot lifecycle need an explicit verification.
- Admin controls currently toggle Portals catalogue visibility; there is no real wallet ledger. Keep all future admin features away from financial posting and settlement.

## 7. Implementation sequence (gates)

### Gate A — security baseline (before payment code)
- Inventory every route and classify public, authenticated, admin and privileged.
- Add automated tests for auth, ownership, IDOR, CSRF/origin, CORS, replay, rate limits, webhook signatures, request limits and idempotency.
- Remove secrets from Git history if any were ever committed; rotate them.
- Add dependency pinning/lockfile, vulnerability scanning, SAST, secret scanning and CI test gates.
- Add secure headers, structured redacted logs, request IDs and abuse controls.
- Move production data to PostgreSQL; test backup restore and migration rollback.
- Verify Railway health, service topology, bot/webhook lifecycle and production environment without exposing secret values.

### Gate B — independent marketplace core
- Replace demo-prefixed entities with production users, listings, orders, offers, deals, disputes and moderation.
- Build the native catalogue and listing model. External marketplace feeds remain optional discovery imports only.
- Enforce ownership and state transitions in the backend and database constraints.
- Add an append-only audit log and a separate double-entry ledger schema before any money is accepted.

### Gate C — payment sandbox
- Confirm legal entity/merchant account, jurisdiction, exact digital-goods classification, terms/refunds/support, settlement/custody model and platform policy.
- Implement Telegram Stars sandbox separately from any external crypto checkout.
- Implement read-only chain watchers and payment verification tests for exact USDT contracts and the customer-approved GRAM contract.
- Add reconciliation, refund/dispute and failure workflows.
- Security review and independent test before enabling production payment credentials.

### Gate D — production launch
- Only after signed-off legal, security and operational reviews: enable one rail at a time, with low limits, monitoring, manual pause switch and rollback plan.
- Run load tests, incident drills, key rotation and backup restore.
- Never deploy live payment flows solely because code compiles.

## 8. Explicitly out of scope until separately approved

- Custodial wallet or private-key management.
- Admin balance editing or hidden credit/debit endpoints.
- Automatic withdrawals or arbitrary transfers.
- Unverified GRAM token contracts.
- Trading, betting, gambling, leverage or margin functionality.
- Enabling crypto checkout inside Telegram for digital goods/services contrary to Telegram's published Stars requirement.
- Production payment processing before sandbox, reconciliation and security review pass.