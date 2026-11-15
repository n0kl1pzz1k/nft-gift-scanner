# TON deposits, withdrawals and seller-owned inventory

Status: TON Connect wallet linking, server-side TON Proof verification for mainnet/testnet, seller NFT import and private listing drafts, dual-provider TonAPI/Toncenter NFT state adapters, the official TonAPI mainnet REST payment observer, and testnet tUSDT payment-intent/signing code are implemented on the draft branch. Testnet payment verification uses the official TonAPI testnet REST API plus TON Center and the frontend builds a TEP-74 transfer request for explicit wallet approval. The mainnet TonAPI and TON Center adapters are wired to the background invoice monitor, but provider credentials and release gates are not configured. Provider credentials, the reviewed collection allowlist and a per-deal escrow deployment path are not yet validated end to end. No escrow contract is deployed by the marketplace, no NFT deposit has been completed, no public listing is enabled, and no real-money operation is enabled. Crypto checkout is disabled inside Telegram Mini Apps; Telegram Stars remain the in-app payment path for digital goods.

## 1. GRAM is the native TON coin

The marketplace asset identifier `GRAM` means the native TON coin. It is not a Jetton:
- mainnet network: `ton-mainnet`
- decimals: 9
- asset kind: `native`
- master/Jetton contract: none (empty by design)

A Jetton with a name or ticker such as GRAM/Gram is not accepted as native GRAM. In particular, the historical PoW Jetton now branded GRM is a different asset and is not the native coin. The application must never infer identity from ticker, logo, metadata or wallet display text.

USDT on TON remains a separate Jetton and is identified only by the Tether-published TON master address:
`EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs`.

References:
- https://www.ton.org/
- https://gramcoin.org/
- https://core.telegram.org/bots/api

## 2. Deposit observation and replay protection

The ledger supports native GRAM and Jetton-specific trace verification. A normalized transfer is eligible for credit only when:
1. It is on TON mainnet and matches the server-created invoice recipient, amount and unique payload.
2. The transaction execution is successful, not bounced and finalized.
3. Native GRAM evidence is a native transfer and contains no Jetton master/opcode. Jetton evidence must match the exact approved master, derived sender Jetton wallet and transfer-notification opcode.
4. Two independently configured providers report the same normalized evidence hash for the same chain event.
5. The database has not already used the chain event or invoice. The event identity is network + transaction hash + event index + asset ID.
6. The payment event and balanced ledger posting commit atomically.

Provider disagreement, provider equivocation, missing finality, unknown asset, stale/malformed trace or provider outage never credits the account. Such events require review. Provider IDs are explicitly configured in `TON_DEPOSIT_PROVIDER_IDS`; two aliases for one operator do not count as independent sources. The official TonAPI mainnet REST adapter and TON Center v3 adapter are connected to the background invoice monitor. Both adapters independently derive and verify source and destination Jetton wallets, transfer-notification opcode, amount, query identity and transaction execution. Runtime credentials and all legal/operational release gates remain unconfigured; crypto deposits stay disabled.

### Mainnet observer configuration

The existing backend worker uses the official TonAPI REST API and TON Center v3. Required runtime variables are:

- `TON_DEPOSIT_PROVIDER_IDS=tonapi,toncenter`
- `TONAPI_API_KEY`
- `TONCENTER_API_KEY`
- `TON_DEPOSIT_ADDRESS`

API keys must be configured only in the existing production secret manager, never committed to the repository or pasted into chat. This config enables observation only; it does not enable Telegram Mini App crypto checkout, escrow deployment, or seller payouts.

## 3. Withdrawals and double-spend prevention

A withdrawal request reserves the user's available liability balance into a request-specific hold in one ledger transaction. The idempotency key is bound to a hash of user, asset, destination, amount and fee; reusing it with different details is rejected.

Lifecycle:
`requested -> processing -> submitted -> confirmed`

Failure/cancel is only releasable while the transaction is definitively not broadcast. After a transaction may have been broadcast, a timeout or uncertain result becomes `reconciliation_required`; the hold remains unavailable and automatic retry/release is forbidden. Confirmation consumes the hold and reduces clearing assets exactly once. The ledger does not sign or broadcast a transfer. A separately reviewed TON signer/treasury execution component, address policy, multi-party approval, fee policy and chain reconciliation are still required. Withdrawal execution remains disabled.

## 4. What users can actually list

Every listing must originate from an asset the seller owns and can transfer. A Telegram profile, wallet address, screenshot, gift link or connected wallet alone is not proof of transferable ownership.

### TON wallet NFTs

- The canonical identity is network + collection address + NFT item/token ID.
- Seller wallet addresses are persisted only after server-side TON Proof succeeds on the matching network. Mainnet NFTs require mainnet proof; the explicit testnet mock asset type requires testnet proof. A wallet proof never substitutes for NFT ownership verification.
- The seller import API calls independent TonAPI and Toncenter adapters. Both must agree on canonical item address, collection, token index and current owner.
- The configured collection allowlist is supplied by `GIFT_RADAR_NFT_COLLECTIONS` as JSON keyed by asset type; no collection can self-register.
- A listing currently starts as a private `draft`. Creating or cancelling a draft does not transfer the NFT or change its custody state. The background escrow monitor can inspect `pending_verification` records, but the current draft flow does not promote a listing into that state because a safe per-deal deposit transaction is not yet implemented.
- Activation requires both providers to report the same successful NFT transfer hash from the seller wallet to the configured escrow, plus current TEP-62 item state showing the exact NFT owned by escrow.
- Only escrow-backed `active` listings appear in the public inventory endpoint. Current seller drafts remain private and are not published.
- The API endpoints are `POST /api/inventory/nft/import`, `POST /api/inventory/listings`, `GET /api/inventory/mine` and `GET /api/inventory/listings`. Listing creation currently returns a private draft.
- TON Proof proves control of a wallet address; it does not prove that a particular NFT belongs to that wallet.
- A listing cannot become active until a marketplace-controlled escrow/deposit address (or an audited atomic-sale contract) is the verified on-chain owner. A seller-supplied transfer hash is only a lookup hint, never proof.
- Ownership must be rechecked before reservation and settlement.

### Collectible usernames and +888 numbers

Only Fragment collectible username NFTs and collectible phone-number NFTs are eligible. They are TON blockchain collectibles and use the same NFT owner/collection verification. An ordinary Telegram username or ordinary phone number is not a collectible asset and cannot be listed as one.

### Gifts shown on a Telegram profile

Telegram distinguishes the profile that hosts a collectible gift from the wallet that owns it. A hosted gift must not be treated as owned by the profile account.

A regular or upgraded gift that is still held in a personal Telegram profile is not automatically a TON wallet NFT. Telegram's Bot API exposes gift-reading and transfer methods for managed Business accounts with the corresponding rights; it does not grant GIFT RADAR access to a personal user's Telegram session. GIFT RADAR must never request a Telegram login code, 2FA password, session file, seed phrase or private key.

For an ordinary personal profile, the safe route is user-controlled export through Telegram's official interface when Telegram allows it, followed by the user's own TON wallet transfer to the marketplace's audited escrow. Only after the resulting NFT transfer is independently verified can it enter escrow inventory. If the gift is not exportable or transferable, it cannot be listed for sale on GIFT RADAR.

Official references:
- https://core.telegram.org/api/gifts
- https://core.telegram.org/api/fragment
- https://core.telegram.org/bots/api
- https://docs.ton.org/contracts/standard/tokens/nft/overview
- https://docs.ton.org/contracts/standard/tokens/nft/nft-reference

## 5. Release boundary

Implemented in this phase:
- TON Connect wallet connect/disconnect, network-aware TON Proof re-verification, and mainnet/testnet wallet separation;
- one-use TON Proof challenges, wallet StateInit/address/signature verification and private sidecar routes;
- official TonAPI mainnet REST payment-trace adapter and TON Center v3 paired observer, with exact source/destination Jetton wallet checks;
- official TonAPI testnet Jetton history adapter paired with TON Center, query-ID and transaction identity matching;
- TON Connect TEP-74 tUSDT testnet payment request construction; wallet submission is not treated as payment confirmation;
- private seller inventory drafts that do not falsely mark an NFT as escrowed;
- explicit native-GRAM vs Jetton identity;
- native transfer verification separate from Jetton transfer-notification verification;
- two-provider observation records and matching-evidence settlement gate;
- withdrawal request idempotency, ledger holds, confirm/release/quarantine state transitions;
- schema support for collectible username/phone-number NFTs and custody states;
- deterministic tests for native GRAM, provider disagreement/replay and withdrawal double-spend protection.

Not yet implemented or enabled:
- production TON payment-trace adapters and continuous invoice scanning;
- safe per-deal/per-listing escrow deployment, TonConnect NFT deposit transaction, seller return/cancel flow and public listing activation;
- successful on-chain testnet tUSDT payment, payout and refund E2E using configured TONAPI/Toncenter credentials and the deployed contract's exact escrow Jetton wallet;
- audited escrow/sale smart contract and its deployment;
- completed seller inventory/import/listing UX beyond private drafts and wallet ownership verification;
- completed buyer payment-to-reservation-to-asset-delivery settlement and seller proceeds ledger workflow;
- Telegram profile gift export/transfer UX;
- TON withdrawal signer, treasury controls and broadcast/reconciliation service. Withdrawal creation is hard-disabled at the API boundary; the history endpoint is read-only. No signer or broadcast code exists;
- end-to-end Stars/TON integration test against real Telegram/TON services.

No live financial or NFT operation may be enabled until those items, the Telegram Stars-only in-Mini-App checkout boundary, Kazakhstan legal review, infrastructure/data-location release gates and independent security review pass.
