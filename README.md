# HASSLE NFT Market

**Independent Telegram NFT-gift marketplace by n0kl1pzz1k.**

HASSLE NFT is a Telegram Mini App marketplace project for publishing listings, browsing assets, receiving buyer requests, making offers and managing seller accounts.

- **Product:** HASSLE NFT / HASSLE NFT Market
- **Mini App:** https://hassle-nft-market.fj7kmzkhn9.workers.dev
- **Backend API:** https://hassle-nft-market.up.railway.app
- **Frontend hosting:** Cloudflare Workers
- **Backend hosting:** Railway
- **Database:** PostgreSQL in the deployed environment
- **Currencies in the product model:** TON and Telegram Stars (XTR)
- **Repository:** legacy GitHub slug `nft-gift-scanner`; product branding is HASSLE NFT

## Current verified state

The public Mini App URL was fetched after the latest successful deployment on 2026-11-20. It rendered the HASSLE NFT catalog, filters, TON/Stars currency selector and buyer-request section. The catalog currently contains **0 public assets**. This is an empty live catalog, not evidence of completed marketplace trades.

GitHub Actions run [44729819248](https://github.com/n0kl1pzz1k/nft-gift-scanner/actions/runs/44729819248) completed successfully:
- Svelte and TypeScript checks
- frontend production build
- Playwright Chromium browser smoke tests
- backend compilation and API/database migration regression tests
- deployment configuration and shell-script syntax checks
- pinned upstream TON multisig-v2 source tests, compilation and deterministic configuration derivation
- Cloudflare Worker deployment

A passing CI run verifies the listed automated checks. It does **not** certify production payments, asset custody, legal compliance, independent security review, provider-side configuration or operational readiness for live settlement.

## Source branch and release status (2026-11-20)

The GitHub repository has active draft branches and open pull requests. Do not treat every feature described in a draft PR as part of the deployed product:
- Block 6 payment/escrow/delivery code was merged into `main` in PR [#214](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/214), but its own PR description says it was not production-deployed at merge time. Real payment flags remain disabled.
- Block 5 marketplace/trading implementation is in open draft PR [#213](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/213), not merged into `main`.
- Block 7 admin/support implementation is in open draft PR [#216](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/216), based on the Block 6 branch, not merged into `main`.
- Other older open PRs also remain and need review before a clean buyer handover.

Railway's backend service is configured to deploy from `main` in the `iad` region. Its variable-name inventory includes PostgreSQL configuration variables and a legacy `PORTALS_API_KEY` name; secret values are not exposed in this audit. The presence of a variable name does not prove that the code currently uses it, but it must be checked before claiming all legacy integrations have been removed.

## Product areas

- Public marketplace catalog, search, filters and sorting
- Listings and buyer requests
- Seller profiles and public seller pages
- Offers and offer status lifecycle
- Favorites, account activity and notifications
- Inventory import and asset ownership verification flows
- TON Connect wallet association and TON Proof verification
- Telegram Business gift connection/import flow
- Admin/support APIs for moderation, disputes, audit and reconciliation review
- Russian and English interface strings

Feature presence in source code is not the same as a fully enabled production workflow. See [Buyer overview](docs/BUYER_OVERVIEW.md) and [Known limitations](docs/KNOWN_LIMITATIONS.md).

## Architecture

- `frontend/`: Svelte 5, TypeScript, Vite, Tailwind CSS, Motion and TON Connect UI.
- `app.py`: FastAPI backend application and HTTP routes.
- `bot.py`: Telegram bot entry point and Mini App launcher.
- `database.py`, `marketplace.py`, `marketplace_offers.py`: persistence and marketplace operations.
- `marketplace_payments.py`, `marketplace_stars.py`: payment-domain and Telegram Stars integration code.
- `marketplace_postgres.py`, `marketplace_offers_postgres.py`: PostgreSQL persistence paths.
- `ton_connect.py`, `ton_nft_chain.py`, `ton_multisig.py`: wallet proof, TON NFT and multisig-related logic.
- `railway.json`: Railway backend deployment configuration.
- `wrangler.toml`: Cloudflare Worker frontend deployment configuration.
- `tests/` and `frontend/e2e/`: backend regression and browser smoke tests.

## Local development

Requirements: Python 3.12+, Node.js 22+, npm.

Backend dependencies:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```

Frontend:
```bash
cd frontend
npm install
npm run check
npm run build
npm run test:e2e
```

Backend checks:
```bash
python -m compileall -q app.py bot.py database.py marketplace.py marketplace_admin.py marketplace_offers.py ton_connect.py ton_multisig.py
python -m pytest -q tests
```

## Deployment configuration

- Railway runs the FastAPI backend using `railway.json`.
- Cloudflare Workers serves the built frontend from `frontend/dist`.
- GitHub Actions validates the backend, frontend and deployment configuration. On pushes to `main`, the frontend job deploys the Worker using GitHub Actions secrets.
- Runtime credentials must be stored in Railway/Cloudflare secret settings, never committed to Git.

See [Buyer overview](docs/BUYER_OVERVIEW.md), [Known limitations](docs/KNOWN_LIMITATIONS.md), [Buyer FAQ](docs/BUYER_FAQ.md), [12-point sale tracker](docs/SALE_PREPARATION_CHECKLIST.md) and [Handover checklist](docs/HANDOVER_CHECKLIST.md) for due diligence and transfer.

## Security and payment boundaries

- No seed phrases or private keys are requested or stored by the application.
- Payment currencies in scope are TON and Telegram Stars (XTR). USDT is not part of the product.
- Real payment feature flags are currently disabled.
- No production TON multisig deployment has been verified.
- No completed live payment-to-delivery-to-payout cycle has been demonstrated.
- No independent security audit or legal opinion is represented as completed.
- A TON multisig source build/test is not an on-chain deployment.
- Manual backup/restore scripts exist; automated provider-side retention/PITR must be verified separately.

Read [Known limitations](docs/KNOWN_LIMITATIONS.md) before relying on any payment, escrow, transfer or production-readiness claim.
