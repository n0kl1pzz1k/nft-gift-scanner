# Production launch readiness

Last reviewed: 2026-11-12

## Current release truth

The deployed service is a read-only Telegram gift catalogue plus a simulated marketplace workflow. Orders, offers and deals are records in the application database; they do not represent settled purchases. Payment adapters, refunds, seller payouts, wallet transactions and NFT transfers are not live.

A Railway persistent volume is mounted at `/data`. This protects the SQLite file from ordinary container replacement when `GIFT_RADAR_DB_PATH` points into that mount. It does not provide multi-writer PostgreSQL semantics, automatic point-in-time recovery, or safe horizontal scaling. The current service has one replica in one region.

## P0 — before accepting real money

- [ ] Product and legal review: seller identity/eligibility, marketplace role, terms, prohibited listings, refunds, disputes, sanctions/AML obligations where applicable, privacy retention, tax and Kazakhstan operating requirements.
- [ ] Managed PostgreSQL provisioned; schema migration and data reconciliation tested against a production-like copy; rollback plan documented.
- [ ] All application instances use the same transactional database; no SQLite file shared across replicas.
- [ ] Payment model approved. Telegram Stars for digital goods/services must use XTR and the platform/bot receives the payment; it is not a direct peer-to-peer seller payout. Seller settlement, fees, reserves and payout timing must be explicitly designed and approved.
- [ ] Payment intent creation is server-side and tied to immutable order amount/currency/participants/expiry.
- [ ] Pre-checkout validation checks user, payload, amount, currency, expiry and current order state.
- [ ] Successful payment is accepted only from authenticated provider evidence; charge IDs and provider event IDs have database uniqueness constraints and idempotent processing.
- [ ] Refund, chargeback/reversal handling, reconciliation and operator audit trail implemented and tested.
- [ ] TON Connect uses user-approved wallet transactions; server validates signed proof and independently verifies the expected on-chain transaction. No server-held user keys or seed phrases.
- [ ] Asset ownership and delivery are independently verified; delivery is idempotent and cannot be triggered by a browser-only callback.
- [ ] Dispute, cancellation and timeout state transitions are role-checked, atomic and covered by race tests.
- [ ] Independent security review and incident-response contact/escalation procedure completed.

## P0 — reliability and abuse controls

- [ ] Database backups configured with retention and a successful restore drill recorded.
- [ ] Migration tested on a copy of the live SQLite volume; row counts, foreign keys, indexes and immutable event histories reconciled.
- [ ] Load tests cover browse/search, login/session refresh, order creation, offer races, payment callbacks and webhook retries at expected peak plus safety margin.
- [ ] Per-account, per-IP and global rate limits are enforced at the edge/API; abuse and spam alerts are monitored.
- [ ] Request body limits, strict input validation, secure headers, CORS allowlist, webhook secret rotation and secret scanning verified.
- [ ] Structured logs contain correlation IDs but never tokens, initData, payment secrets, private keys or personal payment credentials.
- [ ] Metrics and alerts cover error rate, latency, database pool saturation, lock waits, queue age, webhook retries, payment reconciliation gaps and deployment health.
- [ ] A tested rollback and degraded-mode plan exists; feature flags can disable new order/payment creation without blocking account access or support.

## P1 — marketplace operations

- [ ] Seller profile pages and listing ownership are public-safe; no numeric Telegram IDs, session data or private fields leak.
- [ ] Moderation queue, reports, seller restrictions, evidence retention and appeal/support workflows staffed.
- [ ] Reputation derives only from verified completed transactions, never simulated deals.
- [ ] Notifications use an outbox/queue with retry and idempotency rather than relying on in-process tasks.
- [ ] Search/catalogue cache is shared or safely partitioned; upstream providers have independent quotas, timeouts, circuit breakers and stale-data labels.
- [ ] Admin actions require least privilege, strong audit records and break-glass access controls.

## Current architecture constraints to resolve

- SQLite and a mounted Railway volume are suitable only for the current single-instance low-concurrency baseline.
- Process-local caches and refresh tasks are not shared across replicas and can duplicate upstream polling.
- The webhook inbox is transport-level Telegram update deduplication; it is not a payment ledger or fulfillment engine.
- Existing CI previously filtered pull requests to a feature branch rather than `main`. The release-readiness branch corrects this and separates backend, frontend and deployment-config checks.

## Go / no-go rule

Do not enable real payment, payout or NFT-transfer flags until every P0 item is checked with evidence, a production-like end-to-end test has passed, and the responsible operator explicitly approves launch. A green CI run or healthy HTTP endpoint alone is not a launch approval.
