# Open pull-request inventory for buyer due diligence

Snapshot: 2026-11-20. The repository currently has 16 open pull requests. Their existence does not mean they are merged into `main` or deployed. Review each branch before purchase; decide whether to merge, retain as development history or close.

| PR | Title | State | Branch / base | Buyer note |
|---|---|---|---|---|
| [#216](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/216) | Block 7: role-based administration and support | Open, draft | `feat/block7-admin-support-current` → Block 6 branch | Not merged into main. |
| [#213](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/213) | Marketplace offer reservations and deal history | Open, draft | `feat/block5-marketplace-trading` → `main` | Not merged into main. |
| [#89](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/89) | Test atomic rollback of verified NFT ownership transfer | Open | test branch → `main` | Test-only change; review whether still needed. |
| [#88](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/88) | Audit SQLite transaction modes and savepoint semantics | Open | audit branch → `main` | Audit/test branch. |
| [#87](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/87) | Classify PostgreSQL trigger migration behavior | Open | audit branch → `main` | Migration audit branch. |
| [#56](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/56) | Document configurable 2-of-3 escrow authorities | Open, draft | escrow design branch → testnet escrow branch | Design-only; not production deployment. |
| [#55](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/55) | Build TON testnet per-deal NFT escrow contract | Open, draft | testnet escrow branch → payments branch | Testnet prototype; not mainnet deployment. |
| [#54](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/54) | Document verified Phase 0 project state and launch blockers | Open, draft | phase-0 audit branch → `main` | Documentation/audit branch. |
| [#52](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/52) | Phase 0 audit, testnet-only safety gates and API hardening | Open, draft | phase-0 branch → payments branch | Separate development branch. |
| [#51](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/51) | Add private TON Proof verifier and provisioned PostgreSQL/Redis integration foundation | Open, draft | payments branch → `main` | Separate development branch; review against current production config. |
| [#49](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/49) | Phase 0 security audit, webhook idempotency tests, and CI hardening | Open, draft | security audit branch → `main` | Separate security branch. |
| [#48](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/48) | Mini App wallet UX and compliant payments architecture | Open, draft | security/isolated-services → `main` | Separate implementation branch. |
| [#47](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/47) | Security baseline and payment architecture | Open, draft | security/payment-architecture → `main` | Documentation/design branch. |
| [#46](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/46) | Add baseline HTTP response security headers | Open | security headers branch → `main` | Security hardening branch. |
| [#37](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/37) | Document complete GIFT RADAR marketplace architecture | Open | architecture branch → `main` | Historical architecture doc with legacy branding. |
| [#36](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/36) | Define real non-custodial marketplace payment architecture | Open | payment architecture branch → `main` | Historical design branch; payments remain gated. |

## Merged baseline relevant to the current build

- [#214](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/214) Block 6 payments/escrow/admin foundation: merged into `main` on 2026-11-20. Its PR description stated that it was not production-deployed at merge time; the current Railway deploy from `main` must be verified separately.
- [#215](https://github.com/n0kl1pzz1k/nft-gift-scanner/pull/215) is closed as superseded by #216.

## Cleanup recommendation

Do not bulk-merge or bulk-close these PRs as a cosmetic cleanup. First compare their commits with `main`, run tests on the intended release branch, preserve useful audit history, then document the exact release commit delivered to the buyer.
