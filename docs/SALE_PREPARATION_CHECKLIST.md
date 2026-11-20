# HASSLE NFT | 12-point sale preparation tracker

Scope: sale of the entire project and all transferable project-specific assets. The buyer's screenshot package is item 7. All other preparation items are handled by the seller's preparation team.

Status is evidence-based. "In progress" does not mean ready to market.

| # | Workstream | Owner | Status | Evidence / remaining work |
|---|---|---|---|---|
| 1 | Repository audit and cleanup | Assistant | In progress | Public repo inspected; README rewritten; old repository slug and GitHub description remain historical; inspect 16 open issues, branches, secrets references and legacy naming before final handoff. |
| 2 | Main README and architecture map | Assistant | Done | README now explains stack, local setup, deployment, verified state and safety boundaries. |
| 3 | Technical buyer report | Assistant | Done | `docs/BUYER_OVERVIEW.md` describes architecture, product areas, CI evidence, deployment and transfer scope. |
| 4 | Feature inventory | Assistant | Done | Buyer overview lists implemented code areas without claiming all are live commercial workflows. |
| 5 | Known limitations / launch blockers | Assistant | Done | `docs/KNOWN_LIMITATIONS.md` clearly records disabled payments, empty catalog, no verified live escrow cycle, no deployed HASSLE multisig and no independent HASSLE audit. |
| 6 | Infrastructure and deployment map | Assistant | In progress | Cloudflare, Railway, PostgreSQL, Redis, GitHub Actions and live endpoints documented; account transferability, secret inventory and provider settings still need a final verification pass. |
| 7 | Product screenshots and screen recording | User | Waiting for user | Capture the real Telegram Mini App: catalog, filters, offers, profile, inventory and buyer requests. No fabricated listings, payments or trades. |
| 8 | Sale listing in Russian and English | Assistant | Done | `docs/SALE_LISTING_RU.md` and `docs/SALE_LISTING_EN.md` created with accurate disclosures. |
| 9 | Full asset and IP transfer inventory | Assistant | In progress | Proposed scope documented; exact bot username, separately registered domains, creative asset ownership, account transfer mechanisms and any excluded data need confirmation. |
| 10 | Secure buyer handover guide | Assistant | Done | `docs/HANDOVER_CHECKLIST.md` contains 12 handover workstreams covering GitHub, hosting, Telegram, data, secrets, TON, domains and acceptance. |
| 11 | Buyer FAQ and negotiation brief | Assistant | In progress | FAQ/negotiation sheet to be finalized after asset inventory and screenshots; pricing is not yet an appraisal or a fixed commitment. |
| 12 | Final buyer package and marketplace publication | Assistant | Pending | Assemble screenshots with reports/listings, final evidence links, asset schedule and due-diligence folder; publish only after owner approves price and final transfer scope. |

**Important audit finding:** the repository is not a single fully merged release line. Block 6 PR #214 is merged into `main`; Block 5 PR #213 and Block 7 PR #216 remain open drafts. Railway is configured to deploy from `main` in `iad`, and the variable-name inventory still contains `PORTALS_API_KEY`. These points are disclosed in the buyer pack and remain part of cleanup/due diligence.

## Current release evidence

- Mini App live URL: https://hassle-nft-market.fj7kmzkhn9.workers.dev
- Backend URL: https://hassle-nft-market.up.railway.app
- GitHub repo: https://github.com/n0kl1pzz1k/nft-gift-scanner
- Latest documentation CI run: https://github.com/n0kl1pzz1k/nft-gift-scanner/actions/runs/44730851337
- Latest previously completed full deployment CI run: https://github.com/n0kl1pzz1k/nft-gift-scanner/actions/runs/44729819248

## Sale scope rule

The target is a complete-project asset sale, not a code-only license. "Entire project" still means only assets that the seller owns and can legally transfer. Telegram, GitHub, Cloudflare, Railway, domain registrar and third-party provider accounts may require supported transfer or buyer-side recreation. Personal data, wallet secrets, seed phrases and private keys are not sale assets.
