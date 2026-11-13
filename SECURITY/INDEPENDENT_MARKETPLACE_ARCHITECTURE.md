# GIFT RADAR: Independent marketplace architecture

## Non-negotiable product boundary

GIFT RADAR owns its own listings, offers, orders, reservations, seller reputation, notifications and deal lifecycle. It is not a proxy, mirror, frontend or aggregator for Portals, Tonnel, MRKT, Swift Gifts or another marketplace.

### Permitted infrastructure
- TON RPC or archival RPC for on-chain state reads.
- A TON indexer or NFT collection contract reader for canonical NFT ownership facts.
- Telegram Bot API and Mini App authentication.
- Telegram Stars for digital-goods payments inside Telegram, after all release gates pass.
- Internal object storage/CDN for user listing media.

### Prohibited marketplace dependencies
- Importing third-party marketplace listings, order books or floor prices.
- Using aggregator APIs to populate GIFT RADAR's catalogue.
- Redirecting buyers to another market to complete a GIFT RADAR order.
- Treating a provider's listing or a wallet connection as proof of asset ownership.
- Matching or synchronizing external marketplace inventory into native orders.

## Native data model

1. Asset identity: canonical network + collection address + item address for TON NFTs; a separately versioned Telegram gift identity for non-chain gifts.
2. Ownership evidence: verifier, chain block/lt, owner address, evidence hash, verification time and expiry. Evidence is immutable.
3. Listing: seller, canonical asset, integer raw-unit price, currency, status, idempotency key and optimistic version.
4. Reservation: one active lease per listing, buyer, expiry and atomic compare-and-set.
5. Deal: created only from a valid listing + active reservation + fresh ownership proof. Demo orders cannot be promoted or charged.
6. Fulfilment: explicit delivery evidence, buyer confirmation, timeout path and dispute hold.
7. Settlement: append-only ledger entries, Telegram Stars payment/refund events and reconciliation. No balance editing.

## Lifecycle

unverified asset -> verified asset -> active listing -> reserved -> real deal -> paid -> delivery pending -> buyer confirmed -> completed

Exceptional paths: seller cancels before reservation; reservation expires; ownership proof becomes stale; payment fails; delivery times out; buyer opens dispute; refund is requested and reconciled. Every transition is authorized, idempotent, auditable and transactional.

## Ownership verification

- TON Connect proof establishes wallet control only.
- The backend must independently read the canonical NFT item contract and verify its current owner.
- Never trust a client-supplied owner, collection, item address, public key or metadata URL without canonical validation.
- Recheck ownership immediately before listing activation and deal creation.
- Reject stale block data, unsupported collections, malformed addresses, duplicate active listings and ownership changes.
- Telegram gift ownership requires an official, independently verifiable mechanism. Until one exists, that gift type is browse-only and cannot be listed for real settlement.

## UI direction

Visual identity: deep obsidian/graphite surfaces, mineral green and signal-lime accents, restrained glass, high-contrast editorial typography, clear listing cards, strong price hierarchy, compact status chips and a mobile-first bottom navigation. Avoid third-party logos and provider labels. Every screen should communicate that catalogue, orders and deals belong to GIFT RADAR.

## Release gates

Do not enable real deals or Stars until native inventory reads and writes, ownership verification, reservation race tests, fulfilment, dispute/refund handling, reconciliation, abuse controls, load tests and security review pass. Keep production flags false until an explicit release approval.
