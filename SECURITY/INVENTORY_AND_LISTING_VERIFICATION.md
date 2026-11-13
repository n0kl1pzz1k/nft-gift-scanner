# Inventory and listing verification boundary

Status: seller NFT import and pending-listing endpoints, server-verified wallet binding, dual-provider NFT ownership and escrow-transfer verification, and periodic pending-deposit monitoring are implemented on the draft branch. No real-deal checkout, NFT delivery-to-buyer, or seller payout is enabled.

## Canonical identity

Every asset must be keyed by a stable canonical identity, not a display name, thumbnail, rarity label, or user-supplied description.

- TON NFT: `ton-mainnet:<collection-address>:<token-id>`. Normalize the address through the trusted chain adapter before persistence.
- Telegram gift: `telegram:<official-gift-identifier>`. Only an official, documented ownership source may attest ownership.
- Never merge assets across networks or infer a token ID from a display label.

## Ownership evidence

`marketplace_assets.ownership_state='verified'` is a server-derived projection, not a client-editable field. A proof record must identify the authoritative source, a stable evidence reference, a SHA-256 evidence hash, the verified owner, and a bounded expiry. The source adapter must revalidate current ownership before listing activation and again before reservation/payment.

TON Connect Proof proves control of the wallet address. It does not prove that a particular NFT belongs to that wallet. A Portals catalogue result is read-only market data and is not ownership evidence. Screenshots, user-entered addresses, and client-supplied JSON are not authoritative.

## Listing lifecycle

Permitted future progression:

`draft -> pending_verification -> active -> reserved -> sold`

Alternative terminal outcomes: `rejected`, `cancelled`, `expired`.

Only one open listing may exist for a canonical asset. Only one active reservation may exist for a listing. Reservation creation must use a transaction and row lock, bind the buyer, and set a short server-owned expiry. Expiry is evaluated using server time. A stale or expired ownership proof blocks activation and reservation.

Listing state changes must emit append-only `marketplace_listing_events`. Ownership evidence is append-only. Do not store private keys, seed phrases, Telegram login codes, wallet passwords, or user API credentials.

## Implemented seller import and escrow gate

- A wallet is bound to a Telegram user only after server-side TON Proof verification on mainnet. The client cannot submit an arbitrary wallet binding.
- `POST /api/inventory/nft/import` checks the exact collection against the server allowlist and requires matching TonAPI + Toncenter item owner/collection/index results.
- `POST /api/inventory/listings` creates only a private `pending_verification` listing and changes the NFT custody projection to `escrow_pending`.
- A background worker checks pending listings every 20 seconds when both provider credentials, the exact provider IDs and the escrow address are configured.
- Activation requires a matching successful NFT transfer history record from the seller wallet to escrow from both providers, and both current item-state queries must still show the exact item in escrow.
- `GET /api/inventory/listings` exposes only `active` listings whose inventory record is in `escrowed` custody.
- Listing and ownership evidence events are append-only. Duplicate listings and duplicate reservations are constrained by database uniqueness.

## Activation gates

Before any listing is made public:
1. Implement a trusted source adapter for the selected asset type and network.
2. Validate proof authenticity, owner binding, canonical identity, and freshness.
3. Add transactional listing/reservation service methods with concurrency tests.
4. Revalidate ownership immediately before reservation and before any payment approval.
5. Implement delivery evidence and dispute/refund reconciliation.
6. Independently review authorization, rate limits, replay protection, audit logs, backup/restore, and abuse controls.

Until these checks pass, all newly introduced tables are inert schema foundations. Existing demo orders and demo deals remain clearly marked as demo, and Stars checkout remains fail-closed.
