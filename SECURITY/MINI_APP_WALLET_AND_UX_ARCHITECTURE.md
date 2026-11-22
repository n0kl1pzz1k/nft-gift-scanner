# Mini App UX, Wallet Connection and Payment Compliance Architecture

Status: design and implementation requirements. No live payment or wallet signing is enabled by this document.
Branch: security/isolated-services
Date: 2026-11-13

## Product decision

GIFT RADAR is a first-party marketplace. Users should complete discovery, listing, offers, order tracking, support and permitted checkout flows inside one Telegram Mini App. The marketplace backend, catalogue, order state, payment records and audit history are owned by GIFT RADAR. External marketplaces are never the system of record.

A low-friction UX must not bypass platform or payment rules. Compliance is a release gate, not a post-launch patch.

## Telegram wallet connection rules

Per the Telegram Bot Platform Developer Terms, any Mini App wallet functionality must interface with wallets using TON Connect SDK. Do not add WalletConnect, injected EVM providers, arbitrary wallet deep links or direct TRON wallet-connect protocols inside the Mini App.

- TON wallet connect/disconnect and transaction requests use the official TON Connect SDK and a reviewed manifest.
- Wallet connection proves control only through a signed, domain-bound, nonce-based authentication challenge. It does not grant custody or authorize spending.
- Never request or store seed phrases, private keys, wallet passwords or recovery codes.
- Show the exact network, recipient, asset, amount, fee and purpose before any signature request.
- Never auto-open a signing prompt, request broad/unrelated permissions, or sign transactions without a user-initiated action.
- Store wallet addresses and public verification metadata only; encrypt personal data and sensitive identifiers at rest.
- Support multiple wallet addresses per account only with explicit user action, ownership proof and unlink/revocation.
- A connected TON Connect multichain wallet may expose other chain assets according to its own supported capabilities and Telegram's rules. Do not assume a separate TRON wallet protocol is allowed in a Mini App.

Official references:
- Telegram wallet terms: https://telegram.org/tos/bot-developers
- TON Connect overview: https://docs.ton.org/applications/ton-connect/overview
- Telegram Mini Apps: https://core.telegram.org/bots/webapps

## Payment compliance gate

Telegram's official digital-goods payment rules state that sales of digital goods/services inside Telegram bots and Mini Apps must be paid exclusively in Telegram Stars (XTR). This includes digital goods/services sold through an external website if the transaction is still part of the in-Telegram Mini App experience. Do not expose USDT-TRC20, USDT-TON, GRAM or third-party payment checkout as alternative payment buttons for digital goods/services in the Mini App.

Before building a crypto payment flow, obtain a written classification/review of the exact marketplace transaction model from qualified counsel and, where necessary, Telegram/platform review. Do not infer that peer-to-peer settlement, wallet-connect UX, or a webview makes an otherwise restricted digital-goods payment permissible.

### Stars rail

- Stars is the default in-Mini-App checkout for digital goods/services, subject to confirming the exact marketplace/NFT listing model is eligible.
- The backend creates invoices using server-owned order data and currency XTR.
- Verify user identity, order ID, immutable amount, currency, expiry and order state on pre_checkout_query.
- A pre-checkout approval is not payment. Fulfil only after authenticated successful_payment update.
- Persist Telegram charge IDs with unique constraints; process webhook updates idempotently.
- Support refunds using the official refundStarPayment flow and publish /terms, /support and /paysupport.
- Never allow admins to set successful payment flags or directly credit Stars balances.
- Test with Telegram's payment test environment before production.

Official reference: https://core.telegram.org/bots/payments-stars

### Crypto asset support

USDT-TRC20, USDT-TON and GRAM can be researched as future external product capabilities, but are disabled for Mini App checkout unless the exact transaction type is confirmed permitted under current Telegram rules.

- Do not enable GRAM until the customer approves the exact canonical token master contract, decimals, network and settlement model, and those are independently verified.
- Do not treat USDT on TRON and USDT on TON as the same asset. Use separate immutable asset identifiers and contract allowlists.
- Do not collect private keys or sign transactions server-side.
- Any future allowed chain payment observer must be read-only, independently verify finalized on-chain transfers, match exact token contract + destination + amount + payment intent, deduplicate events and reconcile partial/late/overpaid transfers.
- Do not credit accounts from screenshots, client-supplied transaction hashes, unconfirmed events or indexer callbacks alone.
- Any crypto checkout not allowed inside Telegram must not be disguised as an in-app payment or used to circumvent Telegram restrictions.

## Security topology

1. Telegram Mini App frontend: static assets only; no secrets; no authoritative price, ownership, order or payment state.
2. Edge/WAF: TLS, strict origin/CORS allowlist, request size caps, rate limits, bot/abuse detection and security headers.
3. Identity service: Telegram initData verification on backend, short-lived opaque sessions, hashed session identifiers, rotation, revocation and replay prevention.
4. Marketplace API: catalog, profiles, listings, offers, orders, deal state machine, disputes and moderation.
5. Payment orchestrator: Stars invoices, verified webhook intake, idempotency and settlement state machine.
6. Ledger service: append-only double-entry journal. User balances are derived projections, never directly editable fields.
7. Data service: PostgreSQL with least-privilege roles, encryption at rest, TLS in transit, backups, point-in-time recovery and tested restores.
8. Security/audit service: append-only events, redacted logs, alerting, anomaly detection and incident response.
9. Secrets/KMS: Railway Variables or dedicated secret manager, separate credentials per service, rotation and no secret exposure to CI logs or frontend builds.
10. Workers: transactional outbox, durable retries, deduplication and reconciliation.

Prefer strong logical isolation first. Split into separate deployable services only where there is a clear trust boundary, independent scaling need or blast-radius reduction. Do not create many nominal servers sharing the same database owner credential and secret set.

## Owner/admin financial restrictions

- No admin or owner API to edit balances, payment confirmations, transaction IDs, ledger rows or seller proceeds.
- Admin can moderate listings and users through audited non-financial permissions, but cannot post journal entries.
- Historical ledger rows are immutable. Corrections use compensating entries with reason, evidence, actor and independent approval.
- Financial posting is performed only by a narrowly scoped service identity after verified payment evidence and state-machine validation.
- Use database constraints, restricted DB roles and tests to make these restrictions enforceable below the UI layer.

## UX requirements

- Fast launch with skeleton states and cached non-sensitive catalogue metadata.
- Search-first home with collection, gift number, model, backdrop, symbol and price filters when source data supports them.
- One consistent listing card: item identity, seller, ask price, network/asset, freshness, verification status and clear action.
- Listing detail with ownership/provenance evidence, price history, fees, seller reputation and risk notices.
- Clear order timeline: created, awaiting payment, payment verified, escrow/settlement state (if legally and technically supported), completed, disputed/refunded.
- No ambiguous "balance" display: distinguish wallet assets, pending payments, marketplace credits (if any), reserved funds and available proceeds.
- Wallet state always visible but non-intrusive; connect only when needed and explain why.
- Checkout shows final total, asset/currency, fees, seller, order terms, expiry and refund/support route before confirmation.
- Accessibility: large touch targets, readable contrast, keyboard/screen-reader semantics, localization and reduced-motion support.
- Never fake scarcity, fake floor values, fake user counts, fake payment success or fabricated wallet balances.
- Add clear loading, empty, stale-data, partial-outage and retry states. Never silently present stale prices as live.

## Implementation gates

### Gate 1: compliance and threat model
- Classify marketplace transaction types and whether each is a digital good/service for Telegram purposes.
- Review Telegram ToS and App Store / Play Store requirements.
- Produce data-flow diagram, STRIDE threat model, abuse cases and permission matrix.
- Approve allowed in-Mini-App payment methods before coding crypto checkout.

### Gate 2: secure identity and wallet connection
- Implement TON Connect only for Mini App wallet interactions.
- Add nonce-bound wallet ownership proof, session revocation and wallet unlinking.
- Test replay, origin spoofing, malicious manifest, wrong-network and wallet-switch scenarios.

### Gate 3: native marketplace
- Replace simulation-only entities with production listings, orders, offers, deal states, disputes and moderation.
- Move production state from SQLite to PostgreSQL before accepting real funds or high concurrency.
- Add immutable audit log, double-entry ledger schema, idempotency and transactional outbox.

### Gate 4: Stars sandbox
- Implement Stars test environment with server-owned invoices and webhook verification.
- Test duplicate webhook, timeout, stale order, underpayment mismatch, refund and concurrency races.
- No admin override; all payment state derives from verified Telegram updates.

### Gate 5: independent review and release
- SAST, dependency scanning, secret scanning, DAST, auth/IDOR tests, load tests and external security review.
- Backup restore drill, incident response exercise, key rotation and monitoring.
- Enable production payment only after legal, platform and security sign-off.

## Agent/workstream model

Work can be split into independently reviewed workstreams:
- Security architect: threat model, trust boundaries, secret inventory, security acceptance criteria.
- Backend engineer: identity, marketplace state machines, PostgreSQL, ledger and idempotency.
- Mini App engineer: responsive UX, TON Connect integration and accessibility.
- Payments engineer: Stars sandbox and payment reconciliation; crypto remains disabled pending approval.
- QA/security tester: abuse cases, IDOR, replay, concurrency, webhook spoofing, load and regression tests.
- DevOps engineer: Railway topology, isolated service identities, secrets, backups, monitoring and deployment gates.

This repository workflow does not itself provision independent AI workers. Each workstream must be assigned to an actual engineer/agent or handled sequentially with code review; do not claim parallel agents are running unless they are genuinely provisioned.
