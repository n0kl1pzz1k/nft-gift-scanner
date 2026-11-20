# HASSLE NFT | Known limitations and launch gates

Prepared: 2026-11-20. This document is intentionally candid and must accompany any sale presentation.

## Verified now

- Public HASSLE NFT Mini App URL responds and renders the catalog interface after the latest Worker deployment.
- Current public catalog displays zero assets.
- Latest GitHub Actions run 44729819248 completed successfully across frontend, backend, TON multisig upstream and deployment-config jobs.
- Frontend Svelte/TypeScript checks, production build and Playwright browser smoke tests passed.
- Backend module compilation and API/database migration regression tests passed.
- TON multisig upstream source tests and compilation passed; deterministic HASSLE configuration derivation passed.

## Not verified / not enabled

1. A complete live lifecycle from listing through offer, payment, verified transfer and seller payout has not been demonstrated.
2. Real TON payment processing is disabled.
3. Real Telegram Stars payment processing is disabled.
4. No HASSLE production multisig contract deployment has been verified on-chain.
5. No live multisig treasury test transaction has been completed.
6. No independent security audit of HASSLE application/payment/escrow logic is claimed.
7. No legal opinion or complete Kazakhstan/international regulatory review is claimed.
8. No production backup retention/PITR configuration or restore drill is claimed solely from repository scripts.
9. No automated seller withdrawal/payout is claimed. TON payout requires a controlled multisig process; Stars internal credits are not equivalent to an automatic cash withdrawal.
10. Current public marketplace catalog is empty. No real users, revenue or completed transactions are represented.
11. The Telegram bot's live webhook/process status and all Telegram account transfer permissions must be independently checked during handover.
12. Cloudflare/Railway account ownership and provider transferability must be checked. Service URLs are not the same as ownership of a registered domain.

## Payment and asset safety

- Only TON and Telegram Stars (XTR) are in scope. USDT is excluded.
- Never collect or transfer wallet seed phrases/private keys.
- Do not enable payment flags based only on green CI.
- Do not advertise "audited escrow", "automatic payouts", "live payments" or "fully production-ready" without separate evidence.
- Treat every provider transaction as authoritative evidence; screenshots or client-side success messages are insufficient.

## Recommended buyer acceptance gates

- Buyer independently deploys or takes control of Cloudflare and Railway resources.
- Buyer verifies Telegram bot and Mini App launch from Telegram.
- Buyer provisions buyer-owned secrets and rotates all existing credentials.
- Buyer verifies database schema and performs a restore rehearsal in a separate environment.
- Buyer runs end-to-end staging tests for listing, offer, checkout callbacks, delivery, disputes, refunds and payout reconciliation.
- Buyer obtains independent security and legal reviews before enabling live settlement.
