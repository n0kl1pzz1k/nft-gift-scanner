# GIFT RADAR Escrow Architecture

**Status:** DRAFT — owner approval pending. The contract code currently in this branch is an experimental prototype and must not be treated as approved implementation.  
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
4. An unpaid deposited NFT is never automatically returned just because a deadline passed. The seller must raise a dispute, the payment authority must record transaction-history reconciliation evidence proving no successful Stars payment, and both arbitrators must approve the return.
5. A paid deal can be refunded only after Telegram `refundStarPayment` succeeds and the payment authority records the refund reference.
6. No payout before independent buyer-ownership proof.
7. No refund after an ambiguous NFT transfer unless reconciliation proves who owns the NFT and where payment is held.
8. No admin can unilaterally change a terminal deal. Dispute resolution requires the configured multi-approver policy.
9. Contract and backend Ledger are reconciled; disagreement moves the deal to `reconciliation_required`.
10. No key material is stored by the app. Any contract deployment or admin transaction is signed by a user wallet or external HSM; never by a seed phrase stored in the repository or Railway.
11. This branch contains local-emulator tests only. No escrow contract has been deployed to testnet or mainnet. No mainnet deployment is authorized.

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
- The current testnet contract is an NFT custody escrow with an off-chain Stars payment attestation, not a smart contract that holds Stars. Telegram's developer terms say the bot's Stars balance is not the developer's property and Telegram is not a custodian. Stars can be used for Telegram Ads or developer rewards; this is not equivalent to arbitrary seller-to-seller Stars transfers or a segregated client-money account. A legally and operationally valid seller settlement mechanism is a launch blocker.
- Telegram explicitly discourages real-time sales of digital products and virtual items with inherent value that cannot be reclaimed, frozen, rescinded, invalidated or suspended after a successful dispute or chargeback. A high-value NFT marketplace must obtain written specialist advice on this risk before launch.
- The Mini App must provide `/paysupport`, clear `/terms` and `/support` access, and the operator must process legitimate disputes and refunds.
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


## Current implementation and validation evidence

- Tolk contract source: `contracts/GiftRadarEscrow.tolk`.
- Typed opcodes and state definitions: `contracts/escrow-types.tolk`.
- Local Acton emulator suite: 12 scenarios.
- Pinned Acton 1.2.1 CI: build, wrapper generation, lint and emulator tests passed on commit `3ae3487864ebfeee98ff1b9441eecad6e89b3b7c` (workflow run [43505636320](https://github.com/n0kl1pzz1k/nft-gift-scanner/actions/runs/43505636320)).
- A regression found during CI review was corrected: after a dispute is dismissed, a later dispute now starts with a fresh two-arbitrator approval cycle. The timeout test now deploys and deposits before advancing emulated chain time past the deadline.
- This evidence is local-emulator evidence only. It is not a testnet deployment, live NFT transfer, audit, or production readiness proof.

## Remaining integration blockers

1. Backend deal creation must persist the escrow contract address, immutable deal configuration hash, deployment transaction and observed custody proof.
2. Backend must send payment attestations only from the authenticated Telegram successful-payment path, with charge-ID idempotency and Ledger transaction in the same database transaction/outbox boundary.
3. NFT release confirmation must require matching finalized ownership observations from both TON providers, bound to the exact item, buyer, contract, transaction and deal.
4. Seller settlement must not be marked complete from an arbitrary non-empty hash. Backend must validate the actual payout evidence and preserve a durable reconciliation record. Telegram Stars seller payout remains a separate legal/platform-policy blocker.
5. Testnet deployment and full end-to-end exercise remain outstanding. No deployer key or seed phrase may be introduced; use a user wallet or external HSM interface with explicit testnet chain verification.

## Security review: unresolved design decisions (owner approval required)

The current contract prototype is not accepted as a money-safe design. These are mandatory gates, not optional future improvements.

### 1. Adversarial test matrix

The existing 12 emulator scenarios are a smoke-test baseline, not adequate coverage for custody software. Expand to a threat-oriented suite including:
- Wrong NFT item sender; configured item reports a previous owner other than seller; duplicate deposit; deposit after deadline; malformed/truncated message bodies.
- Unauthorized payment attestation; wrong amount; zero/reused Telegram charge reference; duplicate and conflicting attestations; payment after timeout; deadline-boundary payment; replay across deals.
- Buyer-only release; duplicate release; insufficient inbound gas; insufficient retained balance; action-phase send failure; NFT contract rejects transfer; bounced transfer; delayed/duplicate ownership notification; provider outage after transfer.
- No payment through deadline; Telegram refund failure; duplicate refund; conflicting refund evidence.
- Arbitrator 1 only; arbitrator 2 only; duplicate vote; same arbitrator attempts both slots; conflicting decisions; matching decisions with different evidence hashes; no votes; one vote then silence; repeated dispute cycles.
- Quarantine entry from every ambiguous path; recovery when NFT remains escrow-owned, buyer-owned or seller-owned; providers disagree; unauthorized recovery.
- Property/state-machine tests over arbitrary message sequences, fuzzed cell payloads, gas-boundary tests and invariant checks after every failed transaction.

Tests must assert transaction result, post-transaction state, balances and actual NFT owner. A failed transaction must not leave a state that enables settlement or loses custody.

### 2. Payment attestation authority

A single backend paymentAuthority is a critical custody key: compromise permits false payment attestations and may cause irreversible NFT release. Before any testnet end-to-end use, replace the single hot authority model with a reviewed signer design:
- Signing keys live only in an external HSM or threshold signer, never in app environment variables, source, database or logs.
- Contract verifies a threshold of independent authorized signatures, proposed 2-of-3, bound to chain/network, escrow address, deal ID, exact amount/currency, Telegram charge ID hash, action, expiry and unique nonce.
- Per-deal and per-signer limits, replay protection, key rotation/revocation, emergency pause, audit trail and reconciliation.
- Payment evidence originates only from Telegram's authenticated successful-payment update and is reconciled against Telegram charge history and refunds.
- One compromised key must not be sufficient to attest payment or release an NFT.

This requires a contract message/interface redesign and is not implemented by the current prototype.

### 3. Arbitration deadlock and timeout policy

The prototype requires two matching arbitrator votes. If they disagree or one never votes, the state remains disputed indefinitely.

Proposed policy for owner review:
- Use 2-of-3 independent arbitrators, not two fixed keys controlled by one operator.
- Each dispute has a vote deadline and a separate appeal/escalation deadline.
- Disagreement or missed quorum moves to ARBITRATION_ESCALATED; it must not silently count as approval or dismissal.
- Escalation invokes a documented replacement/appeal quorum. No unilateral admin override.
- Default unresolved behavior is freeze and preserve custody, not automatic refund or seller payout. Automatic refund is safe only when the Telegram refund is independently confirmed and NFT return is proven. Seller release is safe only when non-payment is independently reconciled and seller ownership is validated.
- Every path out of dispute has an explicit terminal or reconciliation state and bounded, observable deadlines.

This is fail-closed by design. The legal dispute authority and response SLA must be defined before implementation.

### 4. Quarantine recovery

reconciliation_required must not be a terminal parking lot. Define explicit proof-gated recovery operations:
- Verify actual NFT item owner through independent TON providers and finalized transaction inclusion.
- If NFT remains escrow-owned, threshold-authorized recovery may return it only to a precommitted seller/buyer address under a documented decision.
- If NFT is buyer-owned, confirm delivery and continue only the legally permitted settlement path.
- If NFT is seller-owned, confirm return and refund state before closing.
- If providers disagree, ownership is unprovable or transfer remains pending, retain quarantine and escalate; never blindly retry.
- Every recovery command is idempotent, threshold-authorized, time-bounded and recorded on-chain and in backend audit history.

No unrestricted rescue method or arbitrary recipient parameter is permitted.

### 5. Tolk and independent audit

Tolk is acceptable for an experimental testnet prototype only. Before real funds:
- Independent TON smart-contract audit by reviewers with Tolk/TVM expertise.
- Independent financial/oracle/signing logic audit.
- Reproducible compiler/toolchain and code hash verification.
- Consider a FunC reference implementation or independent formal/state-machine model as a cross-check.
- Fix and retest all critical findings; no unresolved critical/high findings.
- Include audit report and remediation evidence in the buyer data room.

### 6. Per-deal deployment economics

Per-deal deployment has fixed chain cost, storage rent and operational/indexing costs. Viability for small-ticket gifts is unproven.

Benchmark on testnet and estimate mainnet costs from current network parameters: deployment and initialization; NFT deposit and release/refund; storage rent for realistic deal lifetime and long disputes; recovery/arbitration; indexer, RPC and monitoring cost per active deal.

Compare per-deal contracts with a factory/clone pattern and a shared escrow with isolated per-deal storage. Any shared model must preserve strict deal isolation and bounded blast radius. Define who pays deployment/gas, refund treatment if deployment fails, platform subsidy policy and minimum order value. No fee or break-even price is approved until measured.

### 7. Approval sequence and change control

1. Present this architecture and threat model to the owner.
2. Obtain explicit approval of this exact document version.
3. Only then implement the approved design on testnet in a feature branch and PR.
4. Any material change to signer quorum, dispute policy, custody, fees or public message/API interfaces requires a new document revision and explicit approval before implementation.
5. The existing contract code in this branch was written before approval of this exact document. It is prototype work only, not evidence that the approval step occurred. Do not merge, deploy or enable it.

### Current status

- Architecture document: draft, pending owner approval.
- Existing Tolk contract: experimental prototype; not approved.
- Existing 12 emulator tests: baseline only; insufficient for acceptance.
- Testnet deployment: not performed.
- Real-money use: prohibited.