# HASSLE NFT | Infrastructure and configuration map

Prepared: 2026-11-20. This inventory records configuration metadata, not secret values.

## Public endpoints

| Component | Current endpoint |
|---|---|
| Mini App / Cloudflare Worker | https://hassle-nft-market.fj7kmzkhn9.workers.dev |
| Backend / Railway | https://hassle-nft-market.up.railway.app |
| GitHub source | https://github.com/n0kl1pzz1k/nft-gift-scanner |
| CI workflow | https://github.com/n0kl1pzz1k/nft-gift-scanner/actions |

## Railway backend service

- Project: HASSLE NFT Railway project.
- Service display name: `nft-gift-scanner` (legacy service label).
- Source repository: `n0kl1pzz1k/nft-gift-scanner`.
- Source branch: `main`.
- Runtime: Uvicorn / FastAPI.
- Start command: `uvicorn app:app --host 0.0.0.0 --port "$PORT"`.
- Health check: `/ready`.
- Region: `iad` (US East).
- Replicas: 1.
- Persistent volume: `/data`, configured size 500 MB, region `iad`.
- Live domain: `hassle-nft-market.up.railway.app`.

The service is configured for one replica and has a persistent volume. The existence of a volume does not prove that every data path is durable or that backups are scheduled.

## Railway variable-name inventory

The connected Railway integration exposed variable names but redacted values. Names observed:

- Telegram and app: `BOT_TOKEN`, `PUBLIC_APP_URL`, `CORS_ALLOWED_ORIGINS`, `ADMIN_TELEGRAM_ID`, `ADMIN_TELEGRAM_IDS`.
- PostgreSQL selection/configuration: `HASSLE_NFT_DB_BACKEND`, `HASSLE_POSTGRES_DSN`, `HASSLE_POSTGRES_MIGRATION_DSN`, `HASSLE_POSTGRES_SCHEMA`, plus shadow migration controls.
- Redis: `REDIS_URL`.
- TON: `TONAPI_TOKEN`, `HASSLE_TON_CHECKOUT_ENABLED`, `HASSLE_TON_CUSTODY_ACKNOWLEDGED`, `HASSLE_TON_PAYOUT_MODE`, `HASSLE_TON_TREASURY_ADDRESS`, expected multisig address/code/data hashes.
- Telegram Stars: `HASSLE_STARS_CHECKOUT_ENABLED`, `HASSLE_STARS_CUSTODY_ACKNOWLEDGED`, `HASSLE_STARS_PAYOUT_MODE`.
- Operations: `HASSLE_MAINTENANCE_MODE`, `GIFT_RADAR_DB_PATH`.
- Legacy variable name still present: `PORTALS_API_KEY`.

No secret values are included in this file. The legacy variable name is present in Railway's environment inventory. The main application files sampled during the audit (`app.py`, `marketplace.py`, `bot.py`, `marketplace_payments.py`) contained no textual Portals/scanner branding references; `database.py` retains a legacy `GIFT_RADAR_DB_PATH` fallback for compatibility. This is not a complete repository-wide proof that all historical integration code or configuration is gone. Buyer should inspect and remove unused legacy credentials after verifying production dependencies.

The variable name `HASSLE_NFT_DB_BACKEND` and PostgreSQL DSN names are configured, but values were not visible to this audit. Therefore this document does not assert which database backend is currently selected at runtime. Buyer should verify the selected backend, connection target, schema and data consistency.

## Cloudflare frontend

- Worker name in `wrangler.toml`: `hassle-nft-market`.
- Assets directory: `frontend/dist`.
- SPA fallback: enabled.
- Public URL: `https://hassle-nft-market.fj7kmzkhn9.workers.dev`.
- GitHub Actions deployment requires `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` repository secrets. Values are not documented or exported here.

## GitHub Actions

Workflow: `.github/workflows/ci.yml`.

Jobs:
1. Backend Python compilation and pytest regression tests against PostgreSQL service container.
2. Frontend Svelte/TypeScript check, Vite production build, Playwright Chromium smoke tests and Worker deployment on pushes to `main`.
3. Deployment JSON and operational shell syntax validation.
4. Pinned upstream TON multisig-v2 tests, compilation and deterministic HASSLE configuration derivation.

Latest completed deployment run before the sale-documentation commits: [44729819248](https://github.com/n0kl1pzz1k/nft-gift-scanner/actions/runs/44729819248), all four jobs successful including Cloudflare Worker deploy. Subsequent commits only modify documentation; their CI/deployment run is tracked separately in the repository Actions page.

## Transfer recommendations

- Buyer should create/own Railway and Cloudflare accounts, then redeploy or use provider-supported ownership transfer.
- Generate fresh bot, API, database, Redis and CI deployment credentials.
- Rotate all credentials at closing; do not transfer seller's personal login or wallet credentials.
- Verify database engine, region, migrations, backups, restore procedure and data-transfer legality.
- Recheck Telegram bot ownership and webhook setup using Telegram's supported process.
- Verify CORS, Mini App URL, API URL, health/readiness and provider webhook configuration after cutover.
