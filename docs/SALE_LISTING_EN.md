# HASSLE NFT Market | Complete Telegram NFT Marketplace Project

**Asset sale: complete project, not a source-code license.**

HASSLE NFT is an independent Telegram Mini App marketplace project built around Telegram NFT gifts, listings, buyer requests, seller profiles, offers and wallet-verification flows.

## Included in the proposed sale
- Full source repository: Svelte 5 frontend, FastAPI backend, Telegram bot source, tests, deployment scripts and technical documentation.
- HASSLE NFT product identity and project-specific creative assets, subject to ownership verification.
- Existing Cloudflare Worker and Railway deployment configuration, subject to provider transfer rules or redeployment into the buyer's accounts.
- PostgreSQL schema/migration and operational tooling.
- Marketplace, account, offer, inventory, moderation and administrative code.
- TON Connect / TON Proof and Telegram Business integration code.
- CI workflows and test evidence.
- Telegram bot identity and associated Mini App entry point, subject to confirmation and Telegram's supported transfer process.

## Source branch and production disclosure

Block 6 payment/escrow code is merged into the repository's `main` branch, but the original PR records that it was not production-deployed at merge time and payment flags remain disabled. Block 5 trading/offer implementation (PR #213) and Block 7 admin/support implementation (PR #216) remain open draft pull requests and are not merged into `main`. The buyer should assess these branches separately from the deployed application.

The Railway service deploys from `main` in the US East (`iad`) region. Its configured variable names include a legacy `PORTALS_API_KEY`; the value is not disclosed and active use has not been verified. The repository slug and GitHub description also retain legacy scanner branding and need final cleanup.

## Technology
Svelte 5, TypeScript, Vite, Tailwind CSS, Python, FastAPI, PostgreSQL, Redis, aiogram, TON Connect, Cloudflare Workers, Railway and GitHub Actions.

## Live links
- Mini App: https://hassle-nft-market.fj7kmzkhn9.workers.dev
- Backend: https://hassle-nft-market.up.railway.app
- Source: https://github.com/n0kl1pzz1k/nft-gift-scanner

## Verified engineering evidence
Latest CI run 44729819248 passed frontend checks/build/browser smoke tests, backend compilation and regression tests, deployment configuration checks, and pinned upstream TON multisig-v2 tests/build/config derivation. The current public Mini App renders the HASSLE NFT catalog interface.

## Important disclosures
The live catalog currently contains zero assets. No revenue, paying users or completed live trades are represented. TON and Telegram Stars real-payment flags are disabled. A complete live payment-to-delivery-to-payout lifecycle has not been demonstrated. No deployed HASSLE production multisig or independent HASSLE security audit is claimed. Legal and provider-side transfer checks remain necessary before live commercial operation.

The buyer acquires the project as a complete software/business asset package under a written asset purchase and IP assignment agreement. Third-party accounts, subscriptions, credentials, domains and personal/user data are subject to their own ownership, provider rules and applicable law. Seed phrases and private keys are never part of the sale.
