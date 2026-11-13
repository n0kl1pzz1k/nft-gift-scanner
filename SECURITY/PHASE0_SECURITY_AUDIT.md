# GIFT RADAR — Phase 0 Security Audit

Date: 2026-11-13
Status: initial repository audit; NOT a production security certification
Scope: current main branch plus the complete changed-file patches available for PR #47 and PR #48.
Main baseline: c8a9f34a2ea9dffc6bd1f4f79c2d3b55aff95056
Reviewed PR #47 head: 96eb912da3251b96600ab8324be494b9f17215b0
Reviewed PR #48 head: 4bed6c35d404ac5e9de8a0c113b8126daf6e27bc

## Executive result

- No live wallet connection, wallet signing, payment settlement, deposit watcher, or financial ledger is present in the reviewed main-branch application.
- No seed phrase/private-key collection or storage code was found in the reviewed application files or in the PR #47/#48 changed-file patches. This is a scoped finding, not proof about every historical commit, dependency, generated artifact, or unreviewed frontend asset.
- The current admin surface does not expose a balance-edit endpoint. The admin functions reviewed manage catalogue visibility and moderate demo listings/reports.
- Telegram initData verification, opaque sessions, replay prevention for initData exchange, webhook secret validation, payload size checks, and Telegram update inbox deduplication are implemented.
- The existing Telegram update inbox is NOT a payment/chain transaction idempotency system.
- All reviewed crypto asset payment flags in asset_policy.py are false. Stars payments are also disabled in the current application.
- Real-money launch decision: NO-GO. This is expected because payment settlement, ledger, production database, wallet proof, chain verification, and full security/CI validation are not implemented.

## Evidence reviewed

Main branch:
- README.md
- app.py
- marketplace.py
- demo_offers.py
- bot.py
- requirements.txt

PR #47:
- SECURITY_AND_PAYMENTS_ARCHITECTURE.md

PR #48:
- SECURITY/MINI_APP_WALLET_AND_UX_ARCHITECTURE.md
- SECURITY/WALLET_AND_CONTRACT_VERIFICATION.md
- asset_policy.py
- tests/test_asset_policy.py
- inherited SECURITY_AND_PAYMENTS_ARCHITECTURE.md

## Findings

### F0-01 — No wallet secret handling found in reviewed scope
Severity: Informational / pass within scope

README explicitly states that the product does not request or store seed phrases, private keys, wallet passwords, exchange API keys, or account API tokens. The reviewed backend files contain no wallet signing or private-key handling implementation. The PR patches contain architecture text, public token contract identifiers, policy code, and tests; no credential values were identified in those changed files.

Limitations: the repository-wide code search returned no indexed matches, but code search indexing was unavailable/empty. This review did not inspect every frontend file, full Git history, dependency source, build artifact, Railway variables, or external deployment logs. Do not interpret this as a complete secret-history scan. If a real secret was ever committed, revoke/rotate it; deleting it from the latest tree is insufficient.

### F0-02 — Admin balance mutation endpoint not found in reviewed backend
Severity: Pass within current implementation scope

The reviewed admin endpoints expose an overview, toggle Portals catalogue visibility, and moderate demo order reports. No user balance table, financial ledger, credit/debit endpoint, or admin balance mutation operation was found in the reviewed backend. Demo order prices and deal states are simulation records, not balances.

Required future control: keep financial posting out of admin CRUD. Build a double-entry append-only ledger first; corrections must be compensating entries with reason, evidence, actor, and independent approval. No direct SQL balance override.

### F0-03 — Telegram initData and session controls exist
Severity: Pass with follow-up tests required

Observed controls:
- Telegram HMAC validation using the bot token.
- Duplicate/empty initData field rejection.
- Constant-time signature comparison.
- Bounded auth_date freshness with a configurable maximum.
- One-time initData exchange recorded atomically.
- Opaque server-issued session token; only SHA-256 digest persisted.
- Session expiry, individual revocation, and account-wide revocation.
- Admin identity checked server-side against configured Telegram IDs.

Follow-up: add focused automated tests for invalid signatures, stale/future auth_date, duplicate fields, replay, session expiry/revocation, and role/ownership boundaries.

### F0-04 — Webhook transport idempotency exists; payment idempotency does not
Severity: Pass for Telegram update transport / Blocker for future payments

The webhook checks Telegram's secret-token header, enforces a 1 MiB body limit, validates update_id, and uses a durable inbox with payload hashing, a processing lease, duplicate acknowledgement, payload-conflict rejection, and retry after a stale/failed lease.

This only deduplicates Telegram update delivery. There is no Stars charge settlement, chain deposit watcher, payment transaction table, ledger posting, or unique chain-event constraint. A chain transaction must never be credited based on this webhook inbox.

### F0-05 — Crypto payment policy is fail-closed but asset identity helper is not a payment verifier
Severity: Pass for default-off configuration / Blocker for production use

asset_policy.py uses exact asset ID + chain + master address matching, rejects unknown assets and wrong networks, blocks TRON interactions in the Telegram Mini App, omits unapproved GRAM, and keeps payment_enabled false. It does not verify on-chain contract state, transaction traces, recipient, amount, finality, or settlement. It must not be described as proof of payment.

### F0-06 — Telegram payment rails remain disabled
Severity: Pass

README and /api/status state that Stars payments are disabled/not configured and wallet operations/blockchain transactions are false. Do not enable digital-goods checkout until the exact NFT marketplace transaction model is confirmed against Telegram's current rules and Stars flow is implemented and tested.

### F0-07 — CORS allowlist exists; explicit Origin/CSRF enforcement is not established
Severity: Follow-up

app.py configures an explicit CORS origin allowlist and does not allow credentials. CORS is a browser response policy, not authentication and not a substitute for validating Origin/CSRF on sensitive state-changing requests. No separate explicit Origin validation middleware was found in the reviewed app excerpt. Add a documented origin policy and tests before enabling new mutation endpoints.

### F0-08 — Rate limiting is selective, not global
Severity: Follow-up

marketplace.py has persistent per-user action limits for selected operations, including profile updates, reports, and moderation. The reviewed code does not establish a global per-IP/per-account limiter covering all API routes and bot entry points. Add central limits and endpoint-specific budgets before launch.

### F0-09 — Production financial infrastructure is absent
Severity: Blocker

Current marketplace/order/deal persistence is SQLite. The order book and deal lifecycle are explicitly simulation-only. There is no PostgreSQL production ledger, append-only journal, payment settlement table, reconciliation worker, chain watcher, or backup/restore evidence. No real funds may be accepted.

### F0-10 — PR #47 contains an ambiguous crypto checkout sentence
Severity: Documentation correction

The inherited architecture document described a separate external web checkout as a default design, while the wallet/UX document correctly warns that external links/webviews must not circumvent Telegram's Stars requirement. Replace that wording: external crypto checkout is not assumed permissible; it remains disabled unless counsel and Telegram/platform review confirm the exact transaction model is allowed. No external checkout should be used to bypass platform policy.

## Test execution

GitHub Actions run #280 for PR #49 completed successfully:
- Backend: Ruff lint passed for the added security/test modules.
- Backend: Python compileall passed.
- Backend: all 81 unit/regression tests passed, including the three new Telegram update inbox idempotency tests.
- Secret scan: Gitleaks full-history scan passed with no findings reported by the action.
- Frontend: browser JavaScript syntax check, Svelte/TypeScript check, and frontend build passed.
- Deployment configuration validation passed.

This validates the current CI suite and repository state in the PR merge checkout. It does not prove production security, verify live payment settlement, or replace an independent penetration test and legal/platform review. Chain-payment idempotency remains unimplemented because there is no payment settlement or chain watcher yet.

## Release gate

Current decision: NO-GO for real money and production payment activation.

Before reconsideration:
1. Complete a full frontend/backend/history secret scan and rotate any exposed credentials.
2. Add auth, session, ownership, webhook, rate-limit, origin, and idempotency tests.
3. Migrate to PostgreSQL and implement a double-entry ledger with immutable audit.
4. Implement Stars only after product/legal eligibility review and Telegram test-environment validation.
5. Keep crypto rails disabled until a separate written policy approval exists for the exact marketplace flow.
6. Add independent on-chain verification, payment-event uniqueness, reconciliation, refunds, and incident runbooks.
7. Complete staging, load, restore, penetration/security review, and signed release approval.
