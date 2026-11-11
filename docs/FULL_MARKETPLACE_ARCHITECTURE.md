# GIFT RADAR — Full Marketplace Architecture Blueprint

**Status:** target architecture and implementation plan. This document is not a claim that all listed capabilities are already implemented or live. Existing deployed capabilities are identified separately below.

## 0. Product definition

GIFT RADAR is a Telegram-first marketplace for Telegram gifts / NFT gifts and related digital listings. It is a real trading product, not a scanner UI or a simulated order book. The system must support discovery, listings, buy requests, offers, negotiations, order execution, payment verification, asset delivery, disputes, refunds, seller settlement records, moderation and operational support.

### Non-negotiable asset boundary
- Never request or store seed phrases, private keys, wallet passwords, exchange credentials, user API keys or banking login/card credentials.
- Never sign or initiate a wallet transaction without the user's explicit review and approval in their own wallet.
- Never debit, freeze, mutate or claim access to a user's wallet balance.
- Public wallet addresses and transaction data are still sensitive operational data: collect only when necessary, disclose why, protect access and define retention.
- Platform-owned bot/provider credentials may be held in server-side secret storage. They are not user credentials.
- Never promise absolute security. Use defense in depth, independent review, monitoring, incident response and transparent limitations.

## 1. System architecture

### Initial deployment shape
- **Telegram Bot:** onboarding, commands, deep links, notifications, invoice delivery and support entry points.
- **Telegram Mini App (Svelte):** storefront, search, user profile, seller workspace, order room, wallet-connect UX, notifications and help.
- **FastAPI API:** authentication, authorization, marketplace domain services, payment orchestration, moderation and admin APIs.
- **Relational database:** PostgreSQL is the production target for concurrent transactions, constraints, migrations and backups. Existing SQLite storage is acceptable only for the current low-concurrency baseline and must not be treated as a production financial ledger.
- **Background worker:** webhook retries, payment reconciliation, notification delivery, expiry jobs, chain verification and scheduled maintenance.
- **External systems:** Telegram Bot API / Stars, TON Connect-compatible wallets, trusted TON RPC/indexer, optional storage/CDN for media and observability provider.
- **Operations:** Railway deployment, environment-scoped secrets, health checks, structured logs, metrics, alerting and tested backups.

### Logical modules
1. Identity & sessions
2. User profiles and seller verification
3. Catalog, gifts and media metadata
4. Search, filters and discovery
5. Listings and buy requests
6. Offers, negotiation and reservations
7. Orders and state machine
8. Payment adapters (Stars / TON)
9. Asset-delivery verification
10. Seller settlement accounting (not a custodial wallet)
11. Disputes, refunds and support
12. Trust, reputation and moderation
13. Admin, audit and operations

Use domain services and repository boundaries. Keep Telegram/TON provider code behind adapters so domain logic does not depend on a specific provider SDK.

## 2. Identity, accounts and access

- Telegram initData is verified server-side; exchange it for an opaque short-lived session. Store only a token digest.
- Support session listing, current-session logout, logout-all and revocation on suspicious activity.
- Account identity is Telegram user ID; client-provided user IDs are never authoritative.
- Profiles have public display name/avatar/bio and private account metadata. Never expose email, phone or private moderation notes by default.
- Roles: user, seller (capability, not a trust guarantee), moderator, support, finance-operator and administrator. Use least privilege and separate high-risk permissions.
- Admin actions require server-side authorization, reason codes, audit entries and preferably step-up authentication for irreversible operations.
- Rate-limit login, listing creation, offer creation, reports, messaging and payment-intent endpoints.

## 3. Catalog, listings and discovery

### Listing types
- **Sell listing:** seller offers a specific gift/NFT at an asking price.
- **Buy request:** buyer seeks a gift/NFT and posts desired terms.
- **Offer:** buyer makes a price/terms proposal against a listing; seller can accept, reject or counter.
- **Collection / category pages:** curated and searchable groupings.

### Listing fields
- Listing ID, owner ID, asset type, Telegram gift/NFT identifiers where available, title, description, media references, attributes, ask price, currency/payment route, status, created/updated timestamps, expiry, moderation state and revision number.
- Do not accept a client-provided claim of ownership as proof. Define a verification method appropriate to the asset type and available Telegram/TON interfaces.
- Listing edits create revisions. Material changes (price, asset, seller, delivery route) invalidate existing offers/reservations or require explicit reconfirmation.
- Listing status: draft, active, reserved, paused, sold, cancelled, expired, removed, disputed.
- Search supports category, price range, currency, traits, freshness, seller reputation and availability. Avoid exposing hidden user data in search indexes.

### Anti-abuse
- Duplicate listing detection, image/media validation, text length limits, spam throttles, suspicious price-change detection and report workflow.
- Never render remote media unsafely. Restrict content types and sizes; use safe CDN transformations.

## 4. Offers, negotiation and order lifecycle

### Offer model
An offer is immutable once submitted; changes create a new version or counter-offer linked to the previous one. Store buyer, seller, listing, proposed amount/currency, expiry, terms hash, status and timestamps. A party cannot accept their own offer.

### Reservation
Accepting an offer creates a time-limited reservation using a database transaction and compare-and-set update. Only one active reservation can hold a listing. A reservation has an expiry and releases automatically if payment is not initiated/confirmed within policy.

### Canonical order lifecycle
`created → awaiting_payment → payment_pending → paid → delivery_pending → completed`

Alternative states: `cancelled`, `expired`, `payment_failed`, `refund_pending`, `refunded`, `disputed`, `reconciliation_required`.

Every transition:
- checks actor permission and prior state;
- occurs in a transaction;
- uses idempotency keys for retried commands;
- writes an append-only order event;
- emits a durable outbox event for asynchronous work.

Payment and asset delivery are separate state machines. Never mark an order completed merely because payment was received.

## 5. Payment architecture

### Payment adapter interface
A provider adapter creates payment instructions, validates provider events, queries transaction status and requests refunds where supported. Domain services own order validation and state transitions. Frontend callbacks are never payment evidence.

### Telegram Stars
For digital goods/services sold inside Telegram, use Telegram Stars (XTR) wherever Telegram's current rules require it.
- Server derives buyer, seller, order, amount and currency from the database.
- Generate a random, single-use invoice payload bound to order, buyer and expiry.
- Create invoice server-side; do not expose bot token.
- Validate pre_checkout_query (buyer, payload, exact amount, XTR, expiry, order state) and answer within Telegram's deadline.
- Treat successful_payment update as payment evidence only after webhook authentication and strict payload validation.
- Unique constraints on Telegram charge ID, source update ID and invoice reference prevent double-credit.
- Refund via supported Telegram mechanism, tied to original charge, with admin/finance authorization, idempotency and audit.
- Stars credit the bot/platform's Stars balance. They are not automatically paid directly to a seller. Seller settlement, refund liability, platform role, payout rail, timing, fee and dispute process must be agreed before launch.

### TON
- Optional wallet connection through official TON Connect flow.
- Wallet address proof uses a fresh single-use server challenge, domain binding, expiry and signature verification.
- User reviews and approves each transfer in their own wallet.
- Backend independently verifies chain/network, sender, destination/contract, amount, asset, order reference, transaction hash and finality using a trusted node/indexer.
- Store transaction reference and verification evidence, never signing secrets.
- Direct transfer is non-custodial but does not guarantee asset delivery. An atomic swap/escrow contract is a separate scope requiring independent audit and clear risk disclosure.
- Do not use TON as a workaround for Telegram Stars rules on in-app digital goods.

### Accounting and commercial configuration
- No arbitrary user-facing “wallet balance”.
- Maintain an immutable double-entry accounting ledger for platform-side payment events, fees, refunds, seller payable liabilities and payouts once the legal/operational model is approved.
- Ledger entries are append-only; corrections use compensating entries, never silent edits.
- Owner profit, marketplace commission, seller share, payout schedule, minimum payout and refund/dispute liability are **TBD**. Represent them as versioned configuration and contract terms, not hard-coded constants.
- Seller payable records are accounting liabilities, not user-controlled crypto balances and not withdrawable until an approved payout method exists.
- Daily reconciliation compares internal records with Telegram/provider/chain records. Differences enter reconciliation_required and alert operators.

## 6. Asset delivery and ownership verification

- Define a supported delivery method for each asset class before enabling its listing type.
- Verify seller control/ownership using an authoritative platform or chain signal where available; do not trust screenshots or client declarations alone.
- Record delivery intent, sender, recipient, asset identifier, chain/network, transaction reference, verification source, confirmation depth/finality and timestamps.
- Independently verify transfer completion. A seller's “sent” button is not proof.
- For chain transfers, handle pending transactions, dropped transactions, reorgs and wrong-recipient/wrong-asset cases.
- For Telegram-native gifts, document the official transfer mechanism, eligibility, limits and evidence available; do not invent API capabilities.
- If payment succeeds but delivery fails, place the order into delivery_pending/disputed and follow the disclosed resolution policy. Never silently retain funds or falsely report completion.

## 7. Trust, reputation and moderation

- Reputation derives from completed, non-reversed orders and verified outcomes; exclude cancelled and self-dealing activity.
- Show sample size and recency, not just a single opaque score.
- Reviews are allowed only after an eligible completed order; one review per side/order; edits are versioned and abuse-reportable.
- User reports have reason, details, evidence references, status, timestamps and moderator resolution.
- Moderation actions: dismiss, warn, restrict listing, remove listing, temporarily suspend, permanently ban, escalate.
- Separate support access from finance/admin powers. Moderators cannot issue refunds or edit payment ledger.
- Provide appeal path and audit every restriction/removal.
- Detect collusion, wash trading, repeated counterparties, suspicious price patterns, account farms and refund abuse with explainable signals and human review.

## 8. Messaging, notifications and support

- Order room links buyer and seller to one order; keep a durable event timeline.
- Initially use structured order messages/status updates; if free-form chat is added, include block/report, spam limits, abuse review and retention policy.
- Bot notifications: offer received, counter-offer, offer accepted/rejected, reservation expiring, payment pending/confirmed, delivery pending/completed, dispute/refund updates and security alerts.
- Notification delivery uses an outbox and retry queue; duplicate notifications are tolerated but duplicate financial actions are not.
- Do not put session tokens, invoice secrets, private data or full payment payloads in bot messages.
- Support tickets include category, order reference, attachments/evidence, priority, assignee and SLA timestamps.

## 9. Security engineering (application and infrastructure)

- Validate every request at the API boundary; strict schemas, bounded payloads, output encoding and safe error messages.
- Use parameterized SQL, migrations, foreign keys, unique constraints and transaction isolation for races.
- CSRF protections where cookie auth is used; strict CORS, CSP, HTTPS/HSTS and secure headers.
- Protect against IDOR: authorize ownership on every object access, not only at route entry.
- Secrets live only in environment secret storage; never in Git, frontend bundles, logs or support tickets.
- Dependency pinning, automated vulnerability scanning, secret scanning, SAST and reproducible builds.
- Separate dev/staging/production databases, bot tokens, webhook secrets and payment credentials.
- Backups encrypted at rest, access-controlled and restore-tested.
- Admin endpoints private by default, rate-limited, audited and protected with step-up controls for high-risk actions.
- Security incident runbook: contain, revoke/rotate, preserve evidence, assess impact, notify affected parties as legally required, recover and publish postmortem.

## 10. Privacy and data governance

- Collect minimum data needed to operate the marketplace.
- Publish plain-language privacy notice, purposes, retention and user rights before production.
- Separate public profile fields from private identity, payment metadata, support records and moderation evidence.
- Encrypt transport and sensitive stored fields where practical; restrict database access by role.
- Define retention periods for sessions, payment records, support attachments, logs and chain references; legal financial records may need longer retention than ordinary telemetry.
- Provide account export/deletion workflow consistent with mandatory transaction/audit retention.
- Do not send personal data to analytics/AI vendors without a documented purpose, minimization and appropriate safeguards.
- Avoid collecting sensitive wallet linkage unless necessary; public chain data can still reveal behavioral patterns.

## 11. Reliability, performance and disaster recovery

- Production target database: PostgreSQL with managed backups and point-in-time recovery where available.
- Background jobs use durable queue/outbox patterns, bounded retries, dead-letter handling and idempotent workers.
- Webhooks acknowledge only after durable inbox claim; transient failures return retryable responses.
- Health endpoints distinguish liveness and readiness; readiness checks DB and required dependencies.
- Add structured logs with correlation IDs, metrics for API latency/errors, webhook lag, payment reconciliation, order conversion and job failures.
- Define SLOs after baseline measurements; alert on payment/order mismatch, webhook failure, elevated 5xx, DB saturation and backup failure.
- Load-test search, listing, checkout and webhook bursts before launch.
- Document RPO/RTO, backup restore procedure, deploy rollback and database migration rollback strategy.

## 12. Admin, finance operations and audit

### Admin console
- User/account lookup with privacy-aware masking.
- Listing/order/offer inspection and moderation.
- Dispute case management and evidence timeline.
- Payment transaction and reconciliation views.
- Refund request workflow with maker-checker approval for high-value/exception cases.
- Seller settlement report only after commercial/payout model is approved.
- Feature flags and provider health status.

### Audit
Every privileged or financial action records actor, role, action, target, reason, before/after references, timestamp, request/correlation ID and outcome. Never store secrets in audit metadata. Audit events are append-only and exportable for incident investigation.

### Separation of duties
A moderator cannot alter payment evidence. A support operator cannot change ledger entries. A finance operator cannot grant themselves admin privileges. High-risk actions require explicit authorization and, where appropriate, a second approver.

## 13. Compliance, launch governance and release gates

Before real-money production:
- Confirm legal entity/operator role, applicable Kazakhstan and other target-market requirements, consumer terms, seller terms, tax/accounting, AML/sanctions exposure where applicable, privacy, refund and dispute obligations with qualified counsel.
- Obtain required Telegram/provider permissions and comply with Telegram Stars rules for each digital-good surface.
- Define seller onboarding/eligibility, prohibited items, age/region restrictions and takedown process.
- Sign off commercial model: commission, seller proceeds, owner profit, payout method/timing, refund liability, chargeback/abuse allocation and reserves.
- Complete threat model, independent security review, payment flow review and TON contract audit if any contract is introduced.
- Complete end-to-end sandbox/testnet tests and controlled production pilot.
- Publish terms, privacy notice, fee schedule, support contact and incident/refund process.
- Enable production feature flags only after sign-off and operational readiness.

## 14. Core data model (logical)

- `users`, `sessions`, `user_profiles`, `user_roles`
- `assets`, `asset_verifications`, `media_objects`
- `listings`, `listing_revisions`, `buy_requests`
- `offers`, `offer_revisions`, `reservations`
- `orders`, `order_events`, `order_messages`
- `payment_intents`, `payment_events`, `provider_charges`, `refunds`
- `asset_deliveries`, `delivery_evidence`
- `ledger_accounts`, `ledger_entries`, `seller_settlements`, `payouts` (activate only after approved payout model)
- `reviews`, `reports`, `moderation_cases`, `support_tickets`
- `webhook_inbox`, `outbox_events`, `idempotency_keys`
- `admin_audit_events`, `feature_flags`, `reconciliation_runs`

Use UUID/ULID identifiers, UTC timestamps, explicit currency/precision rules, foreign keys, unique provider references and immutable event history. Store money as integer minor units or exact decimal strings with currency-specific precision; never use binary floating point for financial amounts.

## 15. API surface (proposed)

- `POST /api/auth/telegram`, `GET /api/auth/sessions`, `POST /api/auth/logout`, `POST /api/auth/logout-all`
- `GET/POST /api/marketplace/listings`, `GET/PATCH/DELETE /api/marketplace/listings/{id}`
- `GET/POST /api/marketplace/buy-requests`
- `POST /api/marketplace/listings/{id}/offers`, `POST /api/offers/{id}/accept|reject|counter`
- `GET /api/orders`, `GET /api/orders/{id}`, `POST /api/orders/{id}/cancel`
- `POST /api/orders/{id}/payment-intents`, `GET /api/payment-intents/{id}`
- `POST /api/wallet/ton/proof-challenge`, `POST /api/wallet/ton/verify-proof`
- `POST /api/orders/{id}/delivery/claim`, `GET /api/orders/{id}/delivery`
- `POST /api/orders/{id}/disputes`, `POST /api/support/tickets`
- `POST /api/webhooks/telegram` (authenticated Telegram webhook; route may retain current compatibility path)
- Admin/finance routes are separate, role-gated and audited.

The final route names may evolve; do not break existing clients without a versioned migration.

## 16. Delivery roadmap and acceptance gates

### Phase A — foundation and domain cleanup
- Replace demo naming with production domain terminology without breaking existing clients.
- Introduce migrations, PostgreSQL-compatible repository layer, typed domain states and event/outbox patterns.
- Add listing/offer/order ownership checks, revisions, reservation race protection and audit events.
- Acceptance: concurrency tests prove a listing cannot be reserved or sold twice.

### Phase B — marketplace experience
- Profiles, seller pages, listing CRUD, catalog/search/filters, buy requests, offers/counters, order room, bot notifications and moderation.
- Acceptance: end-to-end buyer/seller journey works with test data and no payment claims.

### Phase C — payment foundation
- Payment-intent abstraction, Stars invoice/pre-checkout/success/refund, durable event processing, idempotency and reconciliation.
- TON wallet proof, user-approved transfer request and server-side chain verification.
- Acceptance: sandbox/testnet end-to-end tests, duplicate/race/fraud tests and no secret custody. Production flags remain off until platform/legal/finance approval.

### Phase D — delivery, disputes and settlement
- Asset ownership/delivery verification, disputes, refunds, append-only accounting ledger, seller payable reports and payout integration only after approval.
- Acceptance: payment and delivery cannot be conflated; ledger reconciles; refunds are idempotent; payout policy is documented.

### Phase E — hardening and launch
- Independent security review, penetration test, load test, backup restore drill, incident playbooks, monitoring, terms/privacy/support readiness and controlled pilot.
- Acceptance: signed operational checklist, no critical/high unresolved security findings, rollback verified, support and finance operations staffed.

## 17. Existing implementation versus target

### Already deployed (verified in project history)
- Telegram initData signature validation and freshness checks.
- One-time initData replay protection and opaque digest-stored sessions.
- Session listing, logout and logout-all.
- Authenticated Telegram webhook secret, request size bound and durable update inbox with duplicate/retry handling.
- Existing marketplace baseline includes listings/orders/reporting and simulated fill behavior.

### Not yet implemented or not enabled
- Production payment invoices, pre-checkout and successful-payment ledger.
- Refund execution and payment reconciliation.
- TON Connect, TON proof verification and on-chain payment verifier.
- Asset ownership/delivery verification.
- Production double-entry accounting, seller settlement and payouts.
- Full production admin console, disputes, advanced reputation and support workflows.
- PostgreSQL migration, durable worker/queue and production disaster-recovery guarantees.

**Rule:** only mark a feature “implemented” after code, tests and deployment evidence exist. Only mark “live” after provider configuration, permissions, production validation and operational sign-off exist.
