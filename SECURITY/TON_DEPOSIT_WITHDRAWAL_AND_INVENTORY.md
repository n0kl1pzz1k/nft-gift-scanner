# TON deposits, withdrawals and seller-owned inventory

Status: seller NFT import/listing APIs, dual-provider TonAPI/Toncenter NFT state and transfer-history adapters, a 20-second pending-escrow monitor, and deterministic tests are implemented on the draft branch. Provider credentials, the reviewed collection allowlist and escrow address are not configured here. No escrow contract is deployed, no TON payment provider adapters are enabled, and no real deposit, withdrawal broadcast, NFT transfer or public listing has been enabled.

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

Provider disagreement, provider equivocation, missing finality, unknown asset, stale/malformed trace or provider outage never credits the account. Such events require review. Provider IDs are explicitly configured in `TON_DEPOSIT_PROVIDER_IDS`; two aliases for one operator do not count as independent sources. The normalized deposit monitor and two-provider settlement gate are implemented, but production TonAPI/Toncenter payment-trace adapters and their continuous invoice scanner still need to be connected before crypto deposits can run continuously.

## 3. Withdrawals and double-spend prevention

A withdrawal request reserves the user's available liability balance into a request-specific hold in one ledger transaction. The idempotency key is bound to a hash of user, asset, destination, amount and fee; reusing it with different details is rejected.

Lifecycle:
`requested -> processing -> submitted -> confirmed`

Failure/cancel is only releasable while the transaction is definitively not broadcast. After a transaction may have been broadcast, a timeout or uncertain result becomes `reconciliation_required`; the hold remains unavailable and automatic retry/release is forbidden. Confirmation consumes the hold and reduces clearing assets exactly once. The ledger does not sign or broadcast a transfer. A separately reviewed TON signer/treasury execution component, address policy, multi-party approval, fee policy and chain reconciliation are still required. Withdrawal execution remains disabled.

## 4. What users can actually list

Every listing must originate from an asset the seller owns and can transfer. A Telegram profile, wallet address, screenshot, gift link or connected wallet alone is not proof of transferable ownership.

### TON wallet NFTs

- The canonical identity is network + collection address + NFT item/token ID.
- Seller wallet addresses are persisted only after server-side TON Proof succeeds on TON mainnet. Testnet proofs are not accepted for inventory.
- The seller import API calls independent TonAPI and Toncenter adapters. Both must agree on canonical item address, collection, token index and current owner.
- The configured collection allowlist is supplied by `GIFT_RADAR_NFT_COLLECTIONS` as JSON keyed by asset type; no collection can self-register.
- A listing starts in `pending_verification`, never public. The background escrow monitor polls every 20 seconds when `TON_NFT_PROVIDER_IDS=tonapi,toncenter`, `TONAPI_KEY`, `TONCENTER_API_KEY` and `GIFT_RADAR_ESCROW_ADDRESS` are configured.
- Activation requires both providers to report the same successful NFT transfer hash from the seller wallet to the configured escrow, plus current TEP-62 item state showing the exact NFT owned by escrow.
- Only escrow-backed `active` listings appear in the public inventory endpoint.
- The API endpoints are `POST /api/inventory/nft/import`, `POST /api/inventory/listings`, `GET /api/inventory/mine` and `GET /api/inventory/listings`.
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
- explicit native-GRAM vs Jetton identity;
- native transfer verification separate from Jetton transfer-notification verification;
- two-provider observation records and matching-evidence settlement gate;
- withdrawal request idempotency, ledger holds, confirm/release/quarantine state transitions;
- schema support for collectible username/phone-number NFTs and custody states;
- deterministic tests for native GRAM, provider disagreement/replay and withdrawal double-spend protection.

Not yet implemented or enabled:
- production TON payment-trace adapters and continuous invoice scanning;
- audited escrow/sale smart contract and its deployment;
- frontend seller inventory/import/listing UX (backend endpoints are available on the draft branch);
- completed buyer payment-to-reservation-to-asset-delivery settlement and seller proceeds ledger workflow;
- Telegram profile gift export/transfer UX;
- TON withdrawal signer, treasury controls and broadcast/reconciliation service. The authenticated withdrawal request/history API is present but requires `TON_WITHDRAWALS_ENABLED=true`, which remains disabled; no signer or broadcast code exists;
- end-to-end Stars/TON integration test against real Telegram/TON services.

No live financial or NFT operation may be enabled until those items, the Telegram Stars-only in-Mini-App checkout boundary, Kazakhstan legal review, infrastructure/data-location release gates and independent security review pass.
