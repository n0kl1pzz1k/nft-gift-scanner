# HASSLE NFT marketplace custody, payments and settlement architecture

Status: architecture proposal based on the current repository and public provider documentation. This document does not claim that live payments or automatic payouts are enabled.

## Product target

HASSLE is an independent Telegram Mini App marketplace with:
- one internal GRAM-denominated account balance for marketplace trading;
- deposits and withdrawals initiated by the user from a connected wallet;
- custody and trading of Telegram-native collectible gifts;
- optional custody and trading of on-chain TON NFTs;
- automatic payment reservation, deal settlement, seller credit and auditable withdrawals;
- no USDT, no seed phrase collection, and no operator editing of balances.

GRAM is the product-facing currency label. TON remains the technical blockchain/network name and native-asset denomination in protocol code, APIs, wallet prompts and chain explorers. Do not rename protocol identifiers.

## Findings from comparable products

### Portals model
Portals documents one internal GRAM balance used across products. Users deposit and withdraw from a connected TON wallet; a deposit is credited after network confirmation. Gifts enter through a dedicated Telegram account and are held off-chain while on the marketplace, so internal trades settle without waiting for a blockchain transfer. The chain is used for GRAM deposits/withdrawals, not for each internal gift trade.

### MRKT model
MRKT publicly describes a custodial escrow flow: buyer funds are held, seller transfers the item, buyer gets an inspection window, and funds are released after receipt confirmation. The platform describes automatic cancellation/refund on expiry and internal seller balance credit. These are MRKT's published claims, not a guarantee of its implementation.

### Getgems model
A wallet-centric on-chain marketplace: user-owned NFTs and wallet transactions remain on-chain, with the connected wallet approving operations. This is a useful model for users who do not want to deposit NFTs into Telegram custody.

### Telegram-native gift transfer
Telegram Bot API exposes Business-account gift inventory and transfer methods, subject to Business connection rights. Unique gift transfer requires can_transfer_and_upgrade_gifts; paid transfer can additionally require can_transfer_stars. Recipient chat must have been active within the last 24 hours. This is not a general permission to move gifts from any personal Telegram account. A user must explicitly connect a supported Telegram Business account and grant the necessary rights, or send the gift to a designated marketplace receiving account using a separately supported flow.

### TON Connect
TON Connect securely links a dApp to a user's wallet and lets the app request signatures/transactions without accessing wallet keys. It does not itself monitor or verify chain state; HASSLE must independently verify deposits, NFT ownership changes and transaction finality through a trusted chain/indexer provider.

### Telegram Stars
Telegram's official digital-goods payment flow requires XTR invoices, pre_checkout validation, successful_payment processing, and storage of telegram_payment_charge_id for possible refunds. Stars are a Telegram payment/reward unit, not native GRAM. Do not silently convert Stars into withdrawable GRAM or describe Stars as a crypto deposit. Offer Stars as a separate direct checkout rail only where Telegram policy permits.

## Recommended HASSLE design

Use two asset rails and one common marketplace ledger.

### A. GRAM deposit and internal balance
1. User proves wallet ownership through TON Proof and links the wallet to their Telegram account.
2. Backend creates a deposit intent with a unique reference. Prefer a unique deposit address per user or a unique, verifiable comment/reference if the chosen wallet/provider flow supports reliable attribution. Never credit from a client screenshot or client callback.
3. User confirms the outgoing transfer in their wallet.
4. A chain watcher observes the incoming transaction and independently verifies network, destination, amount, asset type, transaction hash, logical time/trace, confirmation/finality and deposit reference.
5. A reconciliation worker matches the transaction to exactly one unexpired intent and posts one balanced, idempotent ledger transaction.
6. Replays, duplicate provider events, wrong assets, underpayments, overpayments and unmatched deposits go to a reconciliation queue; they must never be silently credited.

### B. Internal purchases and escrow
1. Reserve the listing and buyer funds in one database transaction with row locking.
2. Move funds from buyer available balance to deal escrow using immutable double-entry ledger entries.
3. Mark the deal paid only after the ledger commit succeeds.
4. Transfer the asset through the appropriate rail.
5. Verify actual delivery independently:
   - Telegram gift: successful Telegram Business transfer result plus a fresh recipient inventory read, where supported.
   - On-chain NFT: re-read the NFT item's owner from chain/indexer and verify the exact item address and expected recipient.
6. After verified delivery and the inspection policy, atomically debit escrow, credit seller available balance and credit marketplace fee revenue. The operation must be idempotent.
7. If delivery expires or fails, cancel the deal and return escrow to the buyer. Disputes freeze escrow; no operator may edit ledger balances.

### C. Telegram-native gift custody
- Separate Telegram gifts from on-chain NFTs in the data model using asset rails telegram_business and ton_nft.
- Only accept unique, transferable gifts that can be independently identified.
- Use an explicit supported custody account / Business connection with narrowly scoped permissions.
- Persist Telegram owned_gift_id, unique gift slug, gift number, owner/connection identity, transfer eligibility, transfer fee and last verified time.
- Before listing, ensure the asset is in marketplace custody or a supported atomic delivery method is available. Do not list a gift that merely appeared in a user's read-only personal inventory.
- On withdrawal, delist first, check locks/transferability, request delivery, then mark withdrawn only after Telegram confirms and inventory reconciliation succeeds.
- Do not promise arbitrary personal-account gift transfer through Bot API: the official Bot API transfer method is for a managed Business account and requires its rights.

### D. On-chain NFT custody
Offer two clearly labeled modes:
1. Wallet-held listing: NFT remains in seller's wallet; buyer pays into escrow; seller signs a TEP-62 transfer to buyer/escrow. Listing is not guaranteed deliverable until the transfer is observed. Avoid instant-sale claims unless atomic contract settlement exists.
2. Deposited NFT: seller signs a transfer to a HASSLE escrow contract/address; backend verifies the exact item owner changed to the escrow. Only then enable listing. After sale, release to buyer; on cancellation/refund, return to seller according to escrow state machine.

For strong atomic settlement, use a reviewed escrow smart contract that holds both payment and NFT or coordinates both legs. If the project initially uses a custodial treasury plus off-chain ledger, clearly disclose that it is custodial and maintain strict treasury limits and reconciliation.

### E. Seller withdrawals
- Withdrawal request locks the seller's available balance.
- Risk checks: verified linked wallet, cooldown after wallet change, daily/per-transaction limits, minimum amount, duplicate-address and velocity checks.
- A payout worker creates a signed transaction through an isolated signing service/HSM/KMS or reviewed contract path; it does not read raw keys from application code or logs.
- Broadcast, monitor and reconcile the transaction. Only confirmed chain evidence closes the withdrawal and debits the liability. Failed/expired sends release the reservation.
- Use separate hot operational float with low limits; treasury reserve should not be held in the hot signer.
- 2-of-3 multisig is suitable for treasury administration/reserve movement and emergency recovery, but it is not an automatic payout engine unless a separate automation/signing policy is configured.

## Critical custody decision and current constraint

The repository's docs/TON_MAINNET_MULTISIG_DEPLOYMENT.md explicitly says the planned 2-of-3 mainnet contract is PREPARED, NOT DEPLOYED. The three signer wallets are not themselves a deployed multisig contract. The deterministic address is only an expected address until on-chain deployment and getter/code-hash verification succeed.

No multisig deployment is required merely to implement wallet linking, detect incoming deposits, credit an internal ledger, reserve funds, or perform internal seller credits. However, an automatic on-chain withdrawal/payout requires an authorized signer or deployed contract to sign/execute the outgoing transfer. The user's three public signer addresses cannot sign automatically on the backend, and TON Connect does not grant the marketplace authority to spend from them.

Do not work around this by collecting a seed phrase, putting a user's private key in Railway variables, or claiming that an arbitrary existing wallet/contract is HASSLE-controlled. If no deployment budget exists, implement deposits + ledger + internal trading first, and keep on-chain payouts clearly unavailable until a secure signing path is provisioned. A capped single-signer hot wallet is technically possible but introduces concentrated custodial risk and must not be silently enabled.

## Telegram Stars boundary

Keep Stars as a separate direct checkout rail for eligible digital goods/services:
- create XTR invoice;
- validate buyer, deal, amount and one-time payload at pre_checkout;
- confirm only from successful_payment;
- store charge IDs;
- support Telegram refunds and reconciliation.

Do not treat user-purchased Stars as a generic cash/GRAM top-up balance or promise arbitrary conversion/withdrawal to GRAM. Seller credit and withdrawals for Stars-funded deals require a separately reviewed, policy-compliant settlement design.

## Existing repository components observed

- marketplace_payments.py: payment intents/events, immutable double-entry ledger, escrow event journal and idempotency primitives.
- marketplace_stars.py: Stars pre-checkout and successful-payment settlement logic.
- ton_connect.py: TON Proof challenge and verified wallet binding.
- ton_nft_inventory.py / ton_nft_chain.py: read-only NFT discovery, ownership and collection verification helpers.
- telegram_business_gift_inventory.py: Business gift inventory normalization and a transfer helper; caller must enforce ownership, deal state, Business rights and fee consent.
- docs/TON_MAINNET_MULTISIG_DEPLOYMENT.md: deployment plan, explicitly not deployed.
- docs/PRODUCTION_LAUNCH_READINESS.md and docs/KNOWN_LIMITATIONS.md: real provider flows and automatic payouts are not proven live; Stars/TON flags are disabled.

The current implementation is therefore a foundation, not a complete production payment processor. The highest-risk missing work is the end-to-end TON deposit attribution/reconciliation, treasury signing/payout path, production PostgreSQL cutover, Telegram gift custody/transfer lifecycle, and live staging evidence.

## Implementation sequence

### Phase 0: evidence and fail-closed configuration
- Inventory all payment routes, feature flags, database schemas and deployment variables.
- Ensure all public balance values come only from the ledger.
- Keep provider flags disabled until each end-to-end provider test passes.
- Confirm production DB is managed PostgreSQL before concurrent payment processing.

### Phase 1: automatic GRAM deposits
- Wallet linking and deposit intent creation.
- Unique attribution and chain watcher.
- Confirmations/finality, replay protection, idempotent ledger credit.
- Deposit history and reconciliation queue.
- Staging tests for exact, duplicate, delayed, wrong-network, wrong-asset and unmatched transfers.

### Phase 2: marketplace settlement
- Atomic reserve and escrow ledger movements.
- Telegram Business gift delivery and on-chain NFT delivery adapters.
- Automated deal expiry/refund and seller internal balance credit.
- Dispute freeze and auditable resolution.
- Full buyer/seller lifecycle tests.

### Phase 3: automatic withdrawals
- Select and provision isolated signer/HSM/KMS or audited escrow contract.
- Implement withdrawal reservations, transaction signing, broadcasting, confirmation and retry-safe reconciliation.
- Canary with low limits; treasury reserve separate from hot float.
- Do not enable until signing authority and recovery process are verified.

### Phase 4: Stars as separate checkout
- Verify Bot API invoice/webhook handlers, charge-ID persistence and refunds.
- Do not commingle Stars with GRAM balance.
- Test real Telegram Stars test environment, then controlled live canary.

### Release acceptance criteria
- A deposit is credited exactly once from chain evidence, not from frontend success.
- A purchase cannot overspend or double-reserve.
- Seller is credited only after verified asset delivery.
- A failed/expired deal releases buyer escrow exactly once.
- Withdrawal balance remains locked until payout outcome is reconciled.
- Every ledger transaction balances and is immutable.
- No seed/private key is exposed to app/backend logs or users.
- Provider retries and process restarts do not duplicate money or assets.
- Production flags remain off until independent security/legal review and live controlled acceptance tests are recorded.
