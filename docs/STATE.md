# GIFT RADAR — Repository State and Phase 0 Audit

**Audit date:** 2026-11-14  
**Scope:** repository source, current release-candidate branch, open PR topology, CI evidence and security controls  
**Default branch:** `main` at `c8a9f34a2ea9dffc6bd1f4f79c2d3b55aff95056`  
**Release-candidate baseline reviewed:** `payments/stars-ton-proof-phase-2026-11-13` at `676297fa7c5897291e0db6060c3aa0371b653ce7` (PR #51, draft)  
**This audit branch:** `feat/phase-0-audit`, stacked on PR #51  
**Decision:** **NO-GO for real money, mainnet transfers, production NFT custody or public launch as a completed marketplace. TESTNET-ONLY.**

This is an engineering audit, not an independent penetration test, smart-contract audit, legal opinion, AML assessment or production certification. The main branch is not the same codebase as the payment release candidate. Findings below distinguish deployed/mainline truth from code present only in an unmerged feature branch.

---

## 1. Repository and release topology

The default branch `main` is at commit `c8a9f34a` and is behind the current payment release candidate. PR #51 (`payments/stars-ton-proof-phase-2026-11-13`) is open, draft and targets `main`. PR #49 contains an earlier Phase 0 audit, but its scope was main plus PRs #47/#48 and predates the financial ledger, TON deposit monitor, inventory verification, withdrawal signer and deal settlement modules now present in PR #51.

The old `SECURITY/PHASE0_SECURITY_AUDIT.md` must therefore not be treated as a current audit of the payment branch. In particular, its statements that no ledger, wallet proof or chain monitor exists were true only for its earlier review scope.

This branch is stacked on PR #51 so the audit and safety fixes can be reviewed separately without changing `main`. No branch was merged and no production deployment was made as part of this audit.

## 2. What currently exists

### Backend and identity

- FastAPI application with Telegram Mini App `initData` HMAC verification, freshness checks, replay prevention and opaque server-issued sessions.
- Session tokens are stored as digests; session listing, current-session revocation and account-wide revocation exist.
- Telegram webhook uses a secret header, bounded 1 MiB body, durable update inbox, duplicate detection, payload-conflict detection and retry after a failed/stale processing lease.
- Pydantic request models and generic validation errors are present. The TON Proof service uses Zod schemas and a 16 KiB body limit.
- Admin authorization is server-side through an allowlist of Telegram IDs. Withdrawal approvals require two distinct allowlisted administrators.

### Marketplace and inventory

- User profiles, seller profiles, order records, reports/moderation, favourites, demo offers and demo notifications exist.
- Demo offers/deals are explicitly simulation-only. They are not proof of payment, delivery or a completed trade.
- Native inventory tables, canonical asset identity, seller ownership evidence, listing records, reservation leases and append-only listing/deal events exist on the payment branch.
- Listing activation is designed to require fresh independent ownership/custody evidence. The current NFT providers and inventory schema are mainnet-specific.
- The public catalogue endpoints `GET /api/collections` and `GET /api/gifts` currently return an empty native catalogue. They do not provide the full search/filter/catalogue experience in the product plan.

### Ledger and payments

- `financial_ledger.py` contains an append-only double-entry journal, computed balances, idempotency keys and payment/withdrawal tables.
- Telegram Stars invoice, pre-checkout, successful-payment settlement and refund workflow code exists. This is not a Telegram test-server integration. The normal Bot API is configured from `BOT_TOKEN`.
- TON deposit invoices, provider observations and dual-provider settlement logic exist. The active provider adapters and normalized payment traces explicitly target `ton-mainnet`.
- Withdrawal request reservation, two-admin approval, external HSM signer interface, Toncenter broadcaster and ambiguous-result quarantine exist. The broadcaster URL and withdrawal records explicitly target TON mainnet.
- Deal disputes, pre-transfer refund controls, seller settlement gates, unpaid-deal expiry and a bounded manual reconciliation queue exist.
- These modules are code foundations, not permission to move money. They must remain unavailable in the testnet-only development phase.

### Wallet proof and frontend

- Svelte 5 + Vite Mini App exists.
- TON Connect UI is installed and integrated in the wallet profile flow.
- TON Proof service verifies wallet proof, domain, payload, StateInit-derived address and signature; challenge payloads expire after 600 seconds and are consumed once in a database transaction.
- This Phase 0 branch pins the UI to TON testnet and restricts the proof service/API to network `-3`.
- The existing inventory schema and NFT ownership providers still only accept mainnet. Therefore NFT import/listing is intentionally blocked until a testnet ownership verifier and testnet inventory path are implemented.
- The proof service persists the public wallet address and public key only; no seed phrase or private key is required or stored.

---

## 3. Phase 0 safety changes made on this branch

### Immutable testnet-only release policy

Added `release_policy.py` with code-level constants. They are deliberately not environment variables:

- `TESTNET_ONLY = True`
- `TON_TESTNET_DEPOSIT_ADAPTER_READY = False`
- `TON_TESTNET_WITHDRAWAL_ADAPTER_READY = False`
- `TON_TESTNET_NFT_VERIFIER_READY = False`
- `TELEGRAM_STARS_TEST_ENV_READY = False`

The corresponding capabilities remain false until testnet-specific adapters and end-to-end tests exist. Old Railway flags alone cannot activate them.

Enforcement added:

- Crypto payment policy refuses all currently allowlisted mainnet assets even when every old operational/legal flag is set.
- TON deposit worker exits before polling any provider.
- Withdrawal request creation, withdrawal execution and the broadcaster itself fail closed while the only broadcaster targets mainnet.
- Stars invoice/pre-checkout capability remains disabled without an isolated Telegram test payment environment. An unexpected `successful_payment` update is not posted to the ledger; it is logged for manual reconciliation. The Stars refund API is also disabled in this mode.
- TON Proof accepts testnet network `-3` only. The mainnet RPC endpoint was removed from the proof-service key lookup path.
- TON Connect UI is pinned to `CHAIN.TESTNET`.
- Mainnet NFT import/listing and the NFT escrow monitor remain disabled until a testnet ownership verifier exists.
- Wallet policy reports `ton-testnet`, not mainnet.

### API rate limiting and response hardening

- Changed the edge limiter to cover every `/api/*` route, including public GET routes and `/api/admin/*`, rather than only selected mutation prefixes.
- Per-IP budgets are separated by API route group and HTTP method: 120/minute for reads, 60/minute for mutations and 300/minute for the authenticated Telegram webhook.
- Production requests fail closed when Redis is unavailable or not configured. Development may use the process-local limiter.
- Origin rejection and rate-limit rejection responses now receive the same security headers and `Cache-Control: no-store` treatment as normal JSON responses.
- Existing selected per-user action limits remain in the marketplace domain layer.

These changes provide per-IP route-group protection. They do not yet constitute a complete per-account/global abuse-control system, and real client-IP behavior behind Railway/Cloudflare proxies still needs deployment verification.

---

## 4. Security controls verified in source

| Area | Observed state | Audit note |
|---|---|---|
| Telegram initData | HMAC, freshness, duplicate-field rejection, one-time exchange | Implemented |
| API sessions | Opaque random token, digest-only persistence, revocation | Implemented |
| Object ownership | User IDs are resolved server-side in reviewed marketplace/payment paths | Broad route-by-route OWASP review remains |
| CORS | Exact HTTPS origin allowlist; credentials disabled | CORS is not authentication |
| Mutation origin checks | Explicit Origin allowlist and cross-site fetch rejection | Originless non-browser requests still rely on session authorization |
| Rate limiting | All API routes per IP on this branch; Redis required in production | Proxy client-IP and per-account/global quotas remain |
| Sensitive caching | JSON responses receive `Cache-Control: no-store` | Existing explicit cache headers are preserved by `setdefault` |
| Security headers | nosniff, frame denial, referrer policy, permissions policy, HSTS | CSP and deployment header verification remain |
| Request size | Telegram webhook 1 MiB; TON Proof service 16 KiB | No global FastAPI body-size cap |
| Input validation | Pydantic and Zod schemas; bounded fields on many endpoints | Field bounds/strictness need a complete route inventory |
| SSRF | Audited TON/provider requests use fixed provider URLs or server-only environment URLs | No user-supplied fetch URL was found in the reviewed paths; this is not a full SSRF audit |
| Secrets | Gitleaks full-history job reported no leaks for the PR #51 checkout | Does not inspect Railway variables, external logs or unrelated unmerged history outside the scanned ref |
| Private keys | No seed phrase/private-key collection or persistence path found in reviewed source | HSM interface is external; its service and key custody are not independently audited |
| Database | Alembic migrations and PostgreSQL schema smoke tests exist | Full PostgreSQL business/concurrency test coverage is incomplete |
| Ledger | Append-only double-entry journal, idempotency, computed balances | Property-based invariants and broad PostgreSQL race tests remain |
| Admin | Server-side Telegram ID allowlist; two distinct withdrawal approvers | No complete granular role system or unified admin audit journal |
| Observability | Application logs and worker error logs exist | No verified Sentry, metrics, alert routing or on-call runbook |
| Backups | No repository evidence of a completed backup/restore drill | Must be verified operationally |

---

## 5. Findings and priorities

### P0 — launch blockers

**P0-01 — Physical NFT transfer/escrow is not implemented.**  
There is no TON escrow smart-contract source, reproducible contract build/deployment artifact, audited contract address/code hash, or complete testnet delivery/settlement lifecycle in the repository. The current monitor is an ownership/custody observer, not a transfer contract. A wallet proof or owner observation does not transfer an NFT.  
**Status:** Open. Phase 1 must compare contract, intermediary-account and hybrid architectures, recommend one, and wait for owner approval before implementation.

**P0-02 — Existing TON payment and payout adapters target mainnet.**  
The asset policy, deposit provider normalization, deposit monitor, inventory schema/provider adapters and withdrawal broadcaster contain explicit mainnet assumptions.  
**Status:** Mitigated for this branch by immutable fail-closed testnet gates. The mainnet code remains in the repository and must not be re-enabled. Testnet deposit, withdrawal and NFT adapters are still unimplemented.

**P0-03 — Telegram Stars is not isolated to a test payment environment.**  
The configured Bot API and refund methods can perform real Stars operations if enabled.  
**Status:** Mitigated for this branch by the immutable disabled test-environment gate, a pre-checkout denial, no ledger settlement for unexpected successful-payment updates, and a disabled refund endpoint. A proper Telegram test-server integration is still missing.

**P0-04 — No production release evidence.**  
No completed independent financial/contract audit, Kazakhstan legal/AML/tax approval evidence, verified backups/restore, operational alerts/on-call response or staged limits are present in repository evidence.  
**Status:** Open. Real-money launch is prohibited.

### P1 — required before MVP launch

**P1-01 — Global/per-account abuse controls are incomplete.**  
The Phase 0 branch now rate-limits all API routes per IP through Redis, while selected domain actions also have per-user limits. There is no comprehensive per-account and platform-wide quota policy, and trusted client-IP extraction behind deployed proxies is unverified.  
**Status:** Partially mitigated; remaining work is open.

**P1-02 — No global API request-body limit.**  
The Telegram webhook and TON Proof service have body caps, but ordinary FastAPI endpoints do not share a global cap.  
**Status:** Open.

**P1-03 — PostgreSQL test coverage is narrower than SQLite coverage.**  
The latest passing CI run executed 189 backend tests against the default test database and five PostgreSQL migration/schema smoke tests. It did not run the entire payment/deal concurrency suite against PostgreSQL.  
**Status:** Open.

**P1-04 — Ledger property and concurrency coverage is incomplete.**  
There are balanced-entry, idempotency, append-only, duplicate-payment, deal-state and selected race protections. There is no broad property-based test suite proving ledger invariants across generated operation sequences, nor systematic concurrent purchase/refund/withdrawal tests on PostgreSQL.  
**Status:** Open.

**P1-05 — TON Proof integration tests are incomplete.**  
The verifier implements signature/domain/payload/address checks and atomic one-time nonce consumption, but automated service-level tests for forged proofs, replayed nonce, expired nonce and wallet/network mismatch are not present in the reviewed test suite.  
**Status:** Open.

**P1-06 — Dependency vulnerability scanning was absent from the previous CI.**  
The Phase 0 branch adds `pip-audit` and `npm audit` jobs. Their results are pending this branch's CI run.  
**Status:** In progress.

**P1-07 — Monitoring, alerting and restore evidence are missing.**  
No verified Sentry/metrics integration, alert thresholds/recipients, background-task failure alert, scheduled PostgreSQL backup or restore drill is documented.  
**Status:** Open.

**P1-08 — Admin permissions and audit coverage are incomplete.**  
There is a Telegram ID allowlist and domain-specific immutable events, but not a full role/permission matrix and unified append-only audit journal for every administrative action.  
**Status:** Open.

### P2 — quality and operational follow-ups

**P2-01 — Health endpoint is liveness-only.**  
`GET /health` returns `{"status":"ok"}`; it does not prove PostgreSQL, Redis, TON Proof service or workers are healthy.

**P2-02 — Production readiness documentation is stale relative to the feature branch.**  
`docs/PRODUCTION_LAUNCH_READINESS.md` describes the deployed mainline service as SQLite/read-only/simulation-only. That remains a useful statement about the old deployed baseline, but it is not a full description of PR #51. This file and `SECURITY/PHASE0_SECURITY_AUDIT.md` must be kept clearly scoped to their reviewed refs.

**P2-03 — Backend deployment still uses Railway Nixpacks.**  
There is no root backend Dockerfile. The TON Proof service has its own Dockerfile. A reproducible backend image and complete deployment/rollback runbook remain Phase 7 work.

**P2-04 — Catalogue and UX requirements are incomplete.**  
The native catalogue endpoints return empty data. Full filters, cursor pagination, item price history/floor charts, collection-wide offers, complete deal UX and responsive/theme acceptance tests remain product work.

**P2-05 — Notifications are not a durable outbound delivery system.**  
Demo notification records exist, but there is no production outbox worker with retry, user preferences and provider delivery deduplication for real payment/transfer events.

---

## 6. CI and test evidence

### Last completed baseline run before this Phase 0 patch

GitHub Actions run **#698**, commit `676297fa`, completed with success for all six jobs:

- Backend: lint, Python compilation, Alembic SQLite migration and **189 unit/regression tests passed**.
- PostgreSQL integration: Alembic migration and **5 PostgreSQL schema/append-only smoke tests passed**.
- Frontend: JavaScript syntax, Svelte/TypeScript check and Vite build passed.
- TON Proof service: TypeScript build passed.
- Deployment configuration validation passed.
- Gitleaks full-history scan for the checked-out PR #51 ref reported **no leaks found**.

Run #698 predates the changes in this audit branch. It is baseline evidence only, not proof that the new changes pass. This branch adds dependency audits and additional testnet-only regression tests; the new CI run must complete before this audit step is considered validated.

The PostgreSQL smoke test intentionally emits database error log lines when asserting append-only triggers and uniqueness constraints. Those expected constraint errors were followed by a successful test result.

---

## 7. How to verify the current branch

After the PR CI run completes:

1. Confirm all backend lint and compile steps pass.
2. Confirm the complete unittest discovery run passes, including `tests/test_testnet_only_safety.py`.
3. Confirm `pip-audit -r requirements.txt` reports no unresolved vulnerable dependencies, or triage every finding.
4. Confirm `npm audit --audit-level=high`, `npm run check` and `npm run build` pass.
5. Confirm the TON Proof service builds with testnet-only network validation.
6. Confirm PostgreSQL migrations and append-only constraints pass.
7. Confirm Gitleaks reports no leaks.
8. Confirm no production deploy, main merge, live payment or wallet broadcast occurred.

---

## 8. Remaining risks and next work

1. Keep all money rails and mainnet broadcaster disabled. Do not add Railway flags to bypass `release_policy.py`.
2. Complete the testnet-only API body cap and proxy-aware client-IP verification.
3. Run the full business/concurrency suite against PostgreSQL and add generated Ledger invariant tests.
4. Add service-level TON Proof tests for signature forgery, nonce replay/expiry and domain/network mismatch.
5. Complete the Phase 1 escrow architecture comparison. **Do not write or deploy escrow contract code before explicit approval of the recommended design.**
6. Continue only after the current Phase 0 PR has a green CI run and reviewed findings.

**Release decision remains NO-GO.** A green CI run is necessary, not sufficient, for launch.
