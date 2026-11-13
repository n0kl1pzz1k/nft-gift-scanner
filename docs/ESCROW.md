# GIFT RADAR Escrow Architecture

**Status:** Architecture approved by owner with six conditions on 2026-11-14. Implementation remains experimental and is not approved for deployment.  
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

## Platform-controlled authority configuration

Authority membership is selected exclusively by the marketplace operator from its trusted platform configuration. It is not supplied by either deal participant.

### Source of truth and enforcement points

1. **Platform configuration:** the only source for signer and arbitrator public addresses is the platform's access-controlled authority registry/configuration. Configuration changes require a privileged, audited operator workflow and two-person review. No public API or Mini App field may set or override these addresses.
2. **Deal creation API:** seller and buyer may submit only commercial deal data (NFT item, price, currency and counterparty). The backend loads authority membership from the platform registry, rejects malformed/duplicate/overlapping sets, rejects seller/buyer addresses in either set, fixes the 2-of-3 thresholds, and computes a canonical configuration hash.
3. **Escrow initialization:** the initialization payload carries the platform-generated configuration and its authorization. The contract validates the platform authorization and member constraints, stores the exact configuration snapshot and hash, and exposes them through getters. Seller and buyer messages cannot initialize, replace or amend authority membership.
4. **Privileged actions:** the contract accepts only the required quorum from the addresses in that deal's stored snapshot. Backend configuration is compared with the on-chain snapshot before any payment, release, refund, dispute or recovery message is submitted.
5. **Audit:** every configuration version, approving operator, hash, deal ID and change event is recorded in an append-only audit trail. Missing, stale or invalid configuration fails closed.

**Code status:** these are required enforcement points, not yet implemented. The current prototype still uses one payment authority and two fixed arbitrator fields. The 2-of-3 code and tests must demonstrate that seller/buyer input cannot influence the authority snapshot before this requirement can be marked complete.

## Compromised signer response and maximum deal lifetime

### One signer compromised during an active deal

The 2-of-3 quorum means one compromised signer alone cannot attest Stars payment, approve a dispute outcome, authorize recovery or release the NFT. It also cannot change the authority set.

Required incident procedure:

1. Security monitoring flags the key, pauses new privileged actions for affected deals where the contract supports the pause, and blocks the key in the platform signer registry for new deals.
2. The two remaining original signers independently verify the incident and authorize emergency rotation with **2-of-2 signatures from those remaining members**. The compromised key is removed and replaced with a newly provisioned public key. The threshold remains 2; seller and buyer have no role in this operation.
3. The contract records the old/new configuration hashes, rotation reason, timestamp and signer identities. The rotation is scoped to the affected deal; it cannot change deal economics or recipient addresses.
4. If two uncompromised signers are not available, privileged transitions remain paused and the case enters the published escalation process. No single admin key may bypass quorum.
5. Preserve evidence, rotate credentials in the HSM/signing service, review all signatures since the suspected compromise time, and reconcile every affected deal before resuming.

The contract must reject any action signed only by the revoked key after rotation. A rotation cannot retroactively invalidate a transaction already finalized on-chain. This residual race is why detection, pause and rapid rotation are operational requirements. If two keys are compromised, the 2-of-3 trust assumption has failed and the deal must be escalated; no claim of protection is made.

### Maximum lifetime

- Every deal receives a hard `expires_at` at creation. The maximum lifetime is **30 calendar days from deal creation**; no participant or single operator can extend it.
- The payment/deposit deadline is earlier or equal to `expires_at`. A dispute does not silently extend the deal lifetime.
- A dispute has a first decision window of 7 calendar days and an appeal/escalation window of a further 7 calendar days. The absolute resolution deadline is the earlier of `expires_at` and 14 calendar days after the dispute opens.
- Any extension for a legally mandated process must be approved through the platform's separately governed process, recorded with reasons and a new explicit deadline; it may not be indefinite or self-triggered by a participant.

These are proposed product-policy limits for the first testnet vertical slice. They must be reflected in the contract, backend timers, monitoring, user terms and operational SLA before release.

## Telegram Stars and NFT sequence

The first supported in-Telegram flow is Stars payment plus NFT custody. Stars are not held by the TON contract.

1. **Create deal:** platform fixes the participants, NFT item, amount, deadlines and platform-controlled authority configuration. Seller and buyer accept the displayed terms.
2. **Deposit NFT:** seller explicitly transfers the exact NFT item to the per-deal escrow. Independent TON provider observations confirm that the escrow is the current owner. No Stars invoice is treated as completed at this point.
3. **Pay in Stars:** only after confirmed NFT custody, the buyer pays the exact amount in Telegram Stars (XTR). The backend accepts payment only from Telegram's authenticated successful-payment update, verifies currency/amount/payload and unique Telegram charge ID, then commits the payment and Ledger record idempotently.
4. **Reconcile and wait:** platform signers independently check the successful-payment evidence and relevant Telegram charge/refund history. Once the 2-of-3 payment attestation is accepted, a **24-hour waiting window** begins. The window is a marketplace risk-control policy, not a Telegram guarantee that a payment can never be disputed or refunded.
5. **Release NFT:** only after the payment is confirmed, the full waiting window has elapsed, there is no open dispute/refund/reconciliation hold, and the exact NFT is still escrow-owned, may the 2-of-3 authorized release path transfer the NFT to the buyer. A release message being sent is not delivery proof.
6. **Confirm delivery:** independent TON providers verify finalized buyer ownership for the exact NFT item and deal. Only then can the deal enter delivered.
7. **Seller settlement:** seller settlement is a separate backend/platform operation after delivery. A Ledger entry or Stars charge ID is not proof that Telegram permits or has completed a seller payout. The seller payout mechanism remains a launch blocker until Telegram policy and legal review establish a valid settlement rail.

If payment is not confirmed, the NFT remains in escrow until the bounded expiry/dispute procedure proves a safe return path. If payment is confirmed but a refund is initiated or ownership is ambiguous, automatic release is blocked and the dispute/reconciliation rules apply. No crypto checkout is enabled inside the Telegram Mini App for digital NFTs.

## Final deadlock deadline and default outcome

A dispute cannot remain in the ordinary arbitration state forever:

- Day 0: dispute opens and the configured 2-of-3 arbitrator set is notified.
- By day 7: the initial panel must produce a matching 2-of-3 decision. If it does not, the case automatically enters escalation.
- By day 14, or the deal's hard `expires_at` if earlier: a separately configured platform appeal panel must issue a final 2-of-3 decision. The panel is selected from the platform registry, not by either party, and is disjoint from the initial panel where the platform has enough qualified members.
- The final deadline is visible in the deal UI and service terms. Silence is not consent, and no participant can reset the clock by repeatedly filing the same dispute.

**Default rule at the final deadline:** the system applies the first matching objective-evidence rule below, rather than freezing an NFT indefinitely or allowing an arbitrary admin to choose a recipient:

1. If Telegram confirms no successful Stars payment after charge-history reconciliation, and independent TON observations confirm the NFT is still escrow-owned, return the NFT to the pre-committed seller address.
2. If a successful Stars payment is confirmed, the waiting window has elapsed, no Telegram refund is confirmed, and the NFT is still escrow-owned, deliver the NFT to the pre-committed buyer address. Seller settlement remains a separate obligation and is not fabricated by the contract.
3. If Telegram confirms a refund and TON observations confirm the NFT remains escrow-owned, return the NFT to the pre-committed seller address and close only after both sides are evidenced.
4. If the NFT is already buyer-owned or seller-owned, close only against finalized ownership evidence and the matching payment/refund state.
5. If objective evidence conflicts, the appeal panel must issue a final reasoned direction by the hard deadline. If it cannot, the case is handed to the independent dispute resolver/court named in the service terms. The contract must support a bounded, pre-agreed custody-transfer path to that neutral resolver; it must not accept an arbitrary recipient. The neutral resolver's identity and legal authority must be established before any real-money launch.

The fifth case is a hard launch blocker until the neutral resolver/custody path is contractually and technically specified. The service terms must name the dispute body, jurisdiction, contact method, response SLA and legal effect of its final instruction. The platform must not promise that all disputes can be resolved automatically where the evidence itself is contradictory.

## Testnet cost measurement plan

No deployment price, break-even amount or per-deal fee is approved from estimates alone. The first testnet vertical slice must emit reproducible measurements for:

- Contract deploy and initialization: transaction count, gas/compute, forwarding fees and storage footprint.
- NFT deposit, payment attestation, waiting-window expiry, release, refund, dispute vote, escalation and recovery: transaction count, gas/compute, forwarding fees, message sizes and failure/retry costs.
- Storage rent at 1, 7, 14 and 30 days, including disputed deals held to the maximum lifetime.
- Operational cost per active deal: indexer/RPC requests, provider quorum checks, monitoring/log retention and backend worker load.
- At least 30 repeated testnet runs per principal path, recording median, p95, maximum and failed-run costs. Preserve raw transaction IDs, network config, toolchain version and calculation scripts.
- Compare the measured per-deal contract against factory/clone deployment and a shared escrow with isolated deal state. Include blast-radius and auditability trade-offs, not gas alone.

Report testnet TON amounts separately from projected mainnet TON amounts. Mainnet projections must use a dated network-fee/storage model and an explicit uncertainty range; they are not mainnet measurements. No mainnet transaction is permitted for this exercise. Decide who bears deployment/gas costs, failure refunds and the minimum viable order value only after the benchmark report is reviewed.

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

## Security review: approved conditions and implementation gates

The current contract prototype is not accepted as a money-safe design. The owner approved the following target architecture subject to the conditions recorded here. These remain mandatory implementation and validation gates, not optional future improvements.

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

A single backend paymentAuthority is a critical custody key: compromise permits false payment attestations and may cause irreversible NFT release. Replace it with configurable 2-of-3 signer membership for the first testnet vertical slice. Signer and arbitrator addresses are deployment configuration, never named people or hardcoded identities. The sets are immutable per active deal; the configuration schema is specified in `docs/ESCROW_AUTHORITY_CONFIG.md`. Before any testnet end-to-end use, implement:
- Signing keys live only in an external HSM or threshold signer, never in app environment variables, source, database or logs.
- Contract verifies a threshold of independent authorized signatures, proposed 2-of-3, bound to chain/network, escrow address, deal ID, exact amount/currency, Telegram charge ID hash, action, expiry and unique nonce.
- Per-deal and per-signer limits, replay protection, key rotation/revocation, emergency pause, audit trail and reconciliation.
- Payment evidence originates only from Telegram's authenticated successful-payment update and is reconciled against Telegram charge history and refunds.
- One compromised key must not be sufficient to attest payment or release an NFT.

This requires a contract message/interface redesign and is not implemented by the current prototype.

### 3. Arbitration deadlock and timeout policy

The prototype requires two matching arbitrator votes. If they disagree or one never votes, the state remains disputed indefinitely.

Proposed policy for owner review:
- Use configurable 2-of-3 independent arbitrators, not two fixed keys controlled by one operator. The three addresses are supplied per deal and frozen in its configuration snapshot.
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

1. The owner approved the architecture on 2026-11-14 with six conditions: platform-controlled configurable authorities; final deadlock deadline/default; Stars confirmation and waiting window before NFT release; testnet-measured economics; a minimal 2-of-3 end-to-end testnet slice before expansion; and no mainnet or real funds.
2. Implement in the order: authority configuration and 2-of-3 contract contour; then the first end-to-end testnet scenario; then expand coverage and economics measurement. Report each step with test evidence.
3. Any material change to signer quorum, dispute policy, custody, fees or public message/API interfaces requires a new document revision and explicit approval before implementation.
4. The existing contract code in this branch was written before approval of the exact architecture. It is prototype work only, not evidence that the approval step occurred. Do not merge, deploy or enable it.

### Current status

- Architecture: approved by owner with six conditions on 2026-11-14; implementation gates remain open.
- Platform-controlled authority source, compromised-key procedure, 30-day deal lifetime, 7+7-day dispute windows, 24-hour Stars waiting window and cost benchmark plan: documented; contract/backend implementation pending.
- Existing Tolk contract: experimental prototype; not approved.
- Existing 12 emulator tests: baseline only; insufficient for acceptance.
- Testnet deployment: not performed.
- Mainnet and real-money use: prohibited.