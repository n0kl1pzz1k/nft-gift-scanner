# HASSLE NFT production release gate

Last reviewed: 2026-11-20

## Release state

The feature branch contains the marketplace, payment/escrow, delivery and admin implementation. The latest verified CI run before Block 8 changes passed backend, frontend and deployment-config jobs. This branch is still a draft pull request and is not the production release.

A green CI run proves repository checks only. It does not prove live Telegram, TON, Cloudflare, Railway, PostgreSQL, backups or provider credentials are configured correctly.

## Required release sequence

1. Review and merge the release PR into `main` after owner approval.
2. Confirm the Cloudflare Pages/Worker deployment from `main` completed and the Mini App asset version is current.
3. Confirm the Railway backend deployment is healthy and `/health` returns HTTP 200.
4. Confirm `/ready` returns HTTP 200 and reports database plus required schema as ready. This probe includes identity, listings, offers, payment intents/events, ledger, escrow, dispute approvals, admin audit and TON wallet tables.
5. Confirm production uses managed PostgreSQL, not a shared SQLite file, before enabling concurrent production writes or scaling beyond one instance.
6. Confirm database backup retention and complete a restore drill into a separate empty database/path. Record backup hash, schema hash, row counts and foreign-key/integrity results.
7. Confirm logs, error alerts, latency alerts, database saturation alerts, webhook/payment reconciliation alerts and an incident contact are active.
8. Run the safe read-only smoke/load script against the deployed API. Run authenticated buyer/seller/admin flows only in a dedicated staging environment with test accounts and provider test fixtures.
9. Review the legal/compliance checklist and explicitly approve the marketplace/payment model.
10. Keep TON and Telegram Stars payment flags disabled until all P0 evidence is signed off. Enable each provider separately, first with a controlled canary and verified rollback.

## Database backup and restore

The repository provides `db_backup.py` and `db_restore.py` for SQLite snapshots. A snapshot is integrity-checked, foreign-key-checked, hashed and accompanied by a manifest containing schema/row-count metadata. Restore validates the hash and metadata, restores to a new destination and verifies the restored copy.

Example rehearsal (use a maintenance window and a separate destination; never overwrite the live DB):

```bash
python db_backup.py --source /data/hassle.sqlite3 --output /data/backups/hassle-<timestamp>.sqlite3
python db_restore.py --backup /data/backups/hassle-<timestamp>.sqlite3 --manifest /data/backups/hassle-<timestamp>.sqlite3.manifest.json --output /tmp/hassle-restore-check.sqlite3
```

These commands are a manual SQLite rehearsal, not evidence of an automated scheduled backup. Production PostgreSQL requires managed-provider automated backups/PITR, retention, access controls and a successful restore drill. Those provider-side settings cannot be inferred from repository CI.

## Smoke/load test

`scripts/smoke_load.py` performs concurrent, read-only GET requests to `/health`, `/ready` and the public marketplace stats endpoint. It reports status counts, failures and p50/p95/max latency. It does not create users, listings, orders, payments or transfers. Run it first against staging; do not use it to generate uncontrolled traffic against production.

Authenticated E2E coverage remains in the pytest suite and uses test fixtures/mocked provider responses. No live payment, real NFT transfer, Telegram Business gift transfer or real-money settlement is performed by CI.

## Operational monitoring and response

Monitor at minimum:
- HTTP 5xx rate and p95/p99 latency for API and Mini App.
- `/health` liveness and `/ready` readiness failures.
- PostgreSQL connection-pool exhaustion, slow queries, lock waits, storage and backup age.
- Telegram webhook failures/retries and update inbox age.
- Payment intents stuck in created/pending, provider event deduplication conflicts and reconciliation queue age.
- Escrow deals stalled in payment/delivery/dispute states.
- TONAPI/Telegram provider timeouts, rate limits and circuit-breaker state.
- Deployment failures and frontend/backend version mismatch.

On incident: disable new checkout via feature flags, preserve account access and support, do not manually edit balances or ledger rows, capture correlation IDs and provider transaction references, reconcile from provider evidence, restore only to a separate target, and use the documented rollback procedure.

## P0 go/no-go checklist

- [ ] Product/legal review for Kazakhstan operations, terms, seller eligibility, prohibited assets, refunds/disputes, tax, privacy and applicable AML/sanctions obligations.
- [ ] Release PR reviewed and merged by the owner; deployment from `main` verified.
- [ ] Production managed PostgreSQL provisioned, migration reconciled and rollback rehearsed; no SQLite shared between replicas.
- [ ] Backup retention/PITR configured at the database provider and a restore drill recorded.
- [ ] Telegram bot token, webhook secret, TONAPI credentials and admin allowlist stored only as platform secrets and rotated/verified.
- [ ] CORS origins, Telegram Mini App URL, API base URL, health/readiness URLs and webhook URL match the deployed domains.
- [ ] Provider-backed test scenarios passed in staging: checkout, callback replay, refund, delivery verification, dispute approval, seller payout reconciliation and failure recovery.
- [ ] Independent security review, incident contact and operator runbook signed off.
- [ ] Monitoring and alerts verified with a controlled test alert.
- [ ] Safe smoke/load test passed at the agreed staging target and capacity.
- [ ] Explicit operator approval recorded before any live payment flag is enabled.

## Hard safety boundaries

- Supported payment currencies: TON and Telegram Stars (XTR) only. No USDT.
- No seed phrases or private keys are collected.
- TON seller payout is a manual multisig operation; the app verifies and records provider evidence.
- Telegram Stars seller credit is an internal ledger balance, not an automatic withdrawal.
- No arbitrary fee/commission is enabled.
- CI never makes real payments or transfers.
- A green build or healthy endpoint is not a production launch approval.
