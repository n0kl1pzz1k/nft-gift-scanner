# GIFT RADAR Escrow Architecture

**Status:** approved for testnet implementation by project owner on 2026-11-14  
**Scope:** NFT custody and payment orchestration for Telegram Mini App marketplace  
**Network:** TON testnet only. No mainnet addresses, real transfers, production keys or real funds.

## Decision

Recommend **hybrid escrow**:

1. A dedicated TON escrow instance per deal takes custody of the seller's TEP-62 NFT after the seller explicitly transfers it to the escrow address.
2. The contract enforces deal participants, exact asset identity, payment state, deadlines, and allowed state transitions. For native TON / approved Jettons, funds can be held by the contract and released or refunded by contract rules.
3. FastAPI remains the coordinator and authoritative business ledger. Telegram Stars are confirmed only from Telegram's successful-payment update and recorded as held liabilities in the double-entry Ledger. The backend never treats a client callback as payment evidence.
4. An independent TON observation service verifies NFT ownership and transaction evidence through the configured provider quorum before it asks the escrow to mark delivery as confirmed.
5. Disputes pause automatic settlement. A resolution requires the configured on-chain arbitrator policy and is recorded in the contract and backend audit journal.

This is a hybrid custody design, not a claim that off-chain Telegram Stars are held by a TON contract. The distinction must remain visible in the UI, terms and audit material.

## Telegram payment-policy gate

NFTs and virtual gifts are digital goods. Telegram's Bot Platform Developer Terms require digital goods and services sold inside Telegram bots and Mini Apps to be paid exclusively in Telegram Stars. The official Stars FAQ explicitly says cryptocurrency cannot be used as payment for digital goods inside Telegram apps.

Therefore:
- **Inside the Telegram Mini App:** XTR / Telegram Stars is the only checkout method for NFT digital goods, subject to the platform's current terms.
- **TON / Jetton checkout:** must not be exposed as an alternative payment method inside the Telegram Mini App for digital NFTs. A separate non-Telegram web product would require a distinct legal, distribution and policy review; do not use the Mini App to route around this rule.
- TON Connect may be used for wallet connection, wallet proof and permitted blockchain interactions. Wallet interactions must use TON Connect.
- Telegram Stars balance is not a user-to-user escrow wallet. Telegram states Stars balances are owned by Telegram and may be used by the developer for the purposes described in its terms. The seller payout model for a peer-to-peer NFT marketplace must be reviewed separately. Do not represent that the bot can transfer Stars to arbitrary sellers or that a ledger entry alone creates a transferable Stars liability.

References:
- https://telegram.org/tos/bot-developers (sections 6.2 and 7)
- https://core.telegram.org/bots/payments-stars
- https://core.telegram.org/bots/blockchain-guidelines
- https://docs.ton.org/applications/ton-connect/overview

## Compared models

| Model | Benefits | Main risks | Cost / complexity | Telegram fit |
|---|---|---|---|---|
| A. Fully on-chain TON escrow | Custody and settlement rules are publicly verifiable; native TON and allowlisted Jettons can be held by code; deterministic on-chain state | Contract bugs can lock or lose all deposited assets; async TON messages and Jetton wallet callbacks are not a single atomic transaction; disputes and oracle evidence still need design; upgrades are difficult | Highest. Contract, wrappers, sandbox tests, indexer, deployment, monitoring and independent audit | TON wallet interactions must use TON Connect. Crypto checkout for digital NFTs inside Telegram is not allowed; Stars still needed there |
| B. Platform-controlled intermediate wallet/account | Fastest to prototype; familiar backend orchestration; simple payment integration | Custodial key compromise, insider risk, opaque accounting, single hot-wallet blast radius, legal custody/AML obligations; operator outage can freeze assets | Lowest initial engineering, high ongoing security and legal burden | Stars rules still apply. Wallet integration must use TON Connect. Does not remove custody obligations |
| C. Hybrid per-deal NFT contract + backend Ledger/orchestrator | NFT custody is enforced on-chain per deal; backend can coordinate Telegram payment callbacks, ledger, disputes and user experience; failures can be quarantined without pretending cross-system atomicity | Cross-system settlement is asynchronous; backend observation and Stars confirmation are trust boundaries; payout model and dispute authority need independent review | High, but staged and testable; lower risk than a single omnibus hot wallet | Best fit when Mini App uses Stars for digital goods and TON is used for wallet/NFT custody. Crypto checkout remains disabled in Mini App |

## Deal state machine

Business-visible states:

- `created`: deal and escrow identity created; NFT not yet proven in custody.
- `deposited`: escrow has received the configured NFT item from the configured seller.
- `paid`: payment has been independently verified and is held. XTR payment evidence comes from Telegram's successful-payment update; on-chain payments, where legally permitted outside the Mini App, require contract-level receipt evidence.
- `delivered`: the NFT item is independently verified as owned by the buyer. A transfer message merely being emitted is not sufficient.
- `settled`: seller payout has been confirmed and the deal is terminal.
- `refunded`: buyer refund and return/release of NFT are confirmed, or the deal is explicitly marked as requiring manual reconciliation if one side cannot be proven.

Internal states may include `transfer_pending`, `refund_pending`, and `reconciliation_required`. These must never be collapsed into a successful terminal state.

## Invariants

1. One escrow instance is bound to exactly one deal ID, NFT item address, seller, buyer, currency and amount.
2. Only the configured NFT item can prove deposit; `OwnershipAssigned.prev_owner` must equal the configured seller.
3. Payment is accepted once. The deal ID, provider transaction ID / Telegram charge ID, currency and amount are unique and idempotent.
4. No payout before independent buyer-ownership proof.
5. No refund after an ambiguous NFT transfer unless reconciliation proves who owns the NFT and where payment is held.
6. No admin can unilaterally change a terminal deal. Dispute resolution requires the configured multi-approver policy.
7. Contract and backend Ledger are reconciled; disagreement moves the deal to `reconciliation_required`.
8. No key material is stored by the app. Any contract deployment or admin transaction is signed by a user wallet or external HSM; never by a seed phrase stored in the repository or Railway.
9. All tests, scripts, endpoints and configuration default to testnet. Mainnet execution must be blocked by code, not only by documentation.

## Testnet implementation plan

1. Add a Tolk contract package using the TON-recommended Acton toolchain and a per-deal escrow contract, with typed messages, explicit state transitions, deadline checks, participant checks, and getters.
2. Implement the TEP-62 NFT custody path. Reject unknown NFT item senders and incorrect previous owners.
3. Implement payment evidence adapters: Telegram XTR remains off-chain in the backend Ledger and is attested to the contract only after Telegram webhook confirmation; the contract does not custody Stars. This v1 contract does not accept TON or Jetton payments. No crypto checkout is exposed in the Mini App.
4. Add an explicit backend adapter that binds deal ID, escrow address and transaction evidence. Provider quorum is required before delivery confirmation.
5. Add local TON Sandbox tests for unauthorized calls, wrong NFT item, wrong seller, duplicate payment, amount/currency mismatch, timeout, transfer bounce/ambiguous transfer, dispute and replay.
6. Add testnet-only deployment scripts which refuse any network other than testnet and require an explicit wallet confirmation. Do not deploy automatically in CI.
7. Run a complete testnet flow and retain transaction IDs, contract code hash, build artifacts and reproducible build metadata for external review.

## Scope boundaries and blockers

- Telegram Stars are not an on-chain asset and cannot be moved into a TON smart contract. The contract can hold the NFT while the backend records the Stars payment and controls the subsequent delivery workflow.
- The current testnet contract is an NFT custody escrow with an off-chain Stars payment attestation, not a smart contract that holds Stars. A Stars seller-payout model is not proven by the current Bot API. Telegram's terms describe the bot's Stars balance as owned by Telegram and describe developer rewards; this is not equivalent to arbitrary seller-to-seller Stars transfers. A legally and operationally valid seller settlement mechanism is a launch blocker.
- The current backend may already contain TON/Jetton deposit and withdrawal adapters. Their existence does not authorize crypto checkout for digital goods inside Telegram.
- The TON NFT standard is TEP-62. The NFT item and collection are separate contracts; the item accepts a transfer message and emits ownership-assigned notifications. The marketplace must verify the actual owner after transfer rather than assume a sent message succeeded.
- This implementation is not an audit, legal opinion, AML approval, custody licence, or production readiness certification.

## Acceptance criteria before any real-money launch

- Testnet contract build is reproducible and code hash is recorded.
- The testnet v1 contract explicitly supports NFT custody plus Telegram Stars payment attestations only. On-chain TON/Jetton payment custody is a separate, disabled workstream.
- All local Sandbox tests and backend integration tests pass.
- Testnet end-to-end scenarios prove custody, delivery, payout, refund, timeout and dispute recovery.
- Independent smart-contract audit and financial-logic audit are completed.
- Legal counsel confirms jurisdiction, custody model, AML/KYC duties, sanctions screening, consumer terms, privacy and tax treatment.
- Telegram confirms the Stars checkout and seller settlement model is compatible with its current terms.
- Backups, restore drill, alerting, operational runbooks and incident ownership are verified.
- Mainnet is enabled only through a separate reviewed change after all gates are signed off.
