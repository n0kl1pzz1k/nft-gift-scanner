# HASSLE NFT | Buyer overview

Prepared: 2026-11-20  
Product: HASSLE NFT / HASSLE NFT Market  
Sale scope: complete project and transferable project assets, subject to the final asset schedule and written transfer agreement.

## 1. Executive summary

HASSLE NFT is an independent Telegram Mini App marketplace project focused on Telegram NFT gifts and related collectible assets. The codebase combines a Svelte 5 frontend, FastAPI backend, Telegram bot integration, PostgreSQL persistence paths, TON wallet verification, marketplace listings/offers and administrative support APIs.

The project is live at its current frontend and backend URLs. The frontend was verified after the latest Cloudflare deployment and renders the catalog and marketplace controls. The live public catalog currently contains zero assets. The latest CI run completed successfully, including browser smoke tests and backend regression tests.

This is a software/business asset sale, not a representation that a fully audited, revenue-generating marketplace with live settlement is already operating.

## 2. What is included in the proposed full sale

The intended transaction is for the whole HASSLE NFT project, not a source-code-only license. The final signed asset schedule should enumerate and confirm transferability of:

- HASSLE NFT source repository, commit history, frontend, backend, bot source, tests, scripts and project documentation.
- Product name/brand assets and project-specific visual assets created for the product, to the extent owned and transferable by the seller.
- Telegram bot identity and associated Mini App entry point, subject to Telegram's supported ownership/administration transfer process and confirmation of the exact bot username.
- Cloudflare Worker configuration, deployed frontend project and associated account resources, subject to provider transfer rules or a controlled redeployment into the buyer's account.
- Railway project configuration and backend/database/Redis services, subject to provider transfer rules or a controlled redeployment.
- Project domains and DNS names, if any are separately registered and legally transferable. The current workers.dev and Railway hostnames are provider-managed service URLs, not separately owned domain registrations.
- Database schema, migration and operational scripts. A production data export is not automatically included: user data must be assessed, minimized and transferred only where lawful and contractually authorized.
- Deployment workflows, build configuration, runbooks, technical documentation and test evidence.
- Project-specific integrations and configuration templates. Third-party accounts, API subscriptions, credentials and personal accounts are not automatically transferred; each requires provider-compliant reassignment or buyer-side recreation.

The exact assets, account owners, transfer mechanisms, exclusions and acceptance criteria must be listed in the sale agreement. Do not transfer seed phrases, private keys, personal wallet access or unrelated personal accounts.

## 3. Technology

| Layer | Current implementation |
|---|---|
| Mini App | Svelte 5, TypeScript, Vite, Tailwind CSS, Motion |
| API | Python, FastAPI, Uvicorn |
| Telegram | Bot API / aiogram, Mini App authentication and Business connection flows |
| Data | PostgreSQL deployment path, Redis service, migration/compatibility tooling |
| TON | TON Connect UI, TON Proof and chain verification code |
| Frontend hosting | Cloudflare Workers |
| Backend hosting | Railway |
| CI | GitHub Actions, frontend build and Playwright smoke tests, backend pytest suite |

## 3A. Git branch and production distinction

The repository contains work at different maturity levels. The following distinction is material to due diligence:

- **Block 6:** PR [#214](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/214) is merged into `main`. Its description states that the implementation was not deployed to production at merge time. Payment feature flags are disabled.
- **Block 5:** PR [#213](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/213) remains an open draft based on `main`. Its marketplace trading/offer/reservation work is not merged into `main`.
- **Block 7:** PR [#216](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/216) remains an open draft based on the Block 6 branch. Its role-based admin/support implementation is not merged into `main`.
- Several older draft/open PRs remain in the repository and require a deliberate keep/merge/close decision during cleanup.

A buyer should inspect the main branch and each relevant PR separately. A feature implemented on an unmerged branch must not be represented as part of the currently deployed application.

## 4. Product functionality represented in the codebase

- Marketplace catalog, search, filters and sorting.
- Listing and buyer-request data models and APIs.
- Seller profiles and public profile pages.
- Offer creation and offer state management.
- Favorites, account activity, notifications and deal timelines.
- Telegram gift and TON NFT inventory/ownership-verification flows.
- TON wallet connection and proof-validation code.
- Admin/support APIs for user lookup, moderation, reports, disputes, audit and reconciliation review.
- Payment and ledger domain code for TON and Telegram Stars (XTR), with real-payment feature flags disabled.

These are codebase capabilities and implemented flows. They must not be described as evidence of completed live commercial transactions unless separately demonstrated.

## 5. Deployment and current live endpoints

- Mini App: https://hassle-nft-market.fj7kmzkhn9.workers.dev
- Backend: https://hassle-nft-market.up.railway.app
- GitHub: https://github.com/n0kl1pzz1k/nft-gift-scanner
- Latest verified CI run: https://github.com/n0kl1pzz1k/nft-gift-scanner/actions/runs/44729819248

The live Mini App was fetched after deployment and returned the HASSLE NFT catalog UI. Backend health/readiness had previously been observed healthy. Buyer due diligence should independently repeat endpoint, Telegram launch and account-specific checks at the time of purchase.

## 6. Test evidence

Latest CI run 44729819248: all four jobs completed successfully:
- frontend: Svelte/TypeScript check, production build, Playwright Chromium smoke tests and Cloudflare Worker deployment;
- backend: Python module compilation and API/database migration regression tests;
- TON multisig upstream: pinned upstream contract tests, contract compilation and deterministic HASSLE configuration derivation;
- deployment-config: Railway JSON validation and shell-script syntax checks.

CI does not execute live TON transfers, real Stars charges, real Telegram gift transfers, production seller payouts or an end-to-end live escrow transaction.

## 7. Payment, escrow and TON status

- Intended currencies: TON and Telegram Stars (XTR) only. USDT is excluded.
- Real payment feature flags: disabled.
- No verified production payment-to-delivery-to-seller-payout cycle.
- TON multisig: source build, upstream tests and deterministic configuration derivation are covered by CI; no deployed on-chain HASSLE multisig has been verified.
- Independent escrow/payment security audit: not completed.
- Telegram Stars and TON provider credentials, webhook configuration, operational reconciliation and payout processes require buyer-side verification and controlled staging tests before activation.

## 8. Security and operations

The repository contains Telegram initData validation, session handling, role-protected APIs, request limits, idempotency and database-integrity tooling. The code does not request user seed phrases or private keys. Security implementation should still be independently reviewed before live settlement.

Backup/restore scripts are present. Their existence does not prove a managed backup schedule, point-in-time recovery, retention policy or successful production restore drill. These must be confirmed in the hosting provider accounts.

## 9. Important due-diligence disclosures

- Public catalog currently has zero assets.
- No verified revenue, paying customer base or completed live sales are being represented.
- Real TON/Stars settlement is disabled.
- No deployed production multisig has been verified.
- No independent audit or legal opinion is claimed.
- Third-party hosting, Telegram and provider accounts are subject to their terms and transfer processes.
- Current GitHub repository slug retains the historical `nft-gift-scanner` name even though product branding is HASSLE NFT.
- Repository currently has no LICENSE file; ownership transfer and any future license/assignment should be documented in the purchase agreement. Third-party dependencies retain their own licenses.

## 10. Suggested transaction structure

1. Buyer receives the public demo and repository for inspection.
2. Buyer completes technical and commercial due diligence.
3. Parties sign an asset purchase / IP assignment agreement with a complete asset schedule, representations, exclusions and acceptance criteria.
4. Buyer funds escrow or the agreed payment mechanism.
5. Seller transfers repository ownership, project-specific assets and supported account resources, or redeploys services into buyer-controlled accounts.
6. Buyer rotates all credentials and validates deployments.
7. Buyer confirms acceptance; payment is released under the agreement.

This is an operational outline, not legal advice. A qualified lawyer should prepare/review the agreement and data-transfer terms.
