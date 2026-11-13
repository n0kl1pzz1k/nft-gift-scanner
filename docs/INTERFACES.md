# Public Interfaces

This file records externally observable API and on-chain interfaces. Update it in the same pull request whenever a public route, request/response schema, webhook contract or smart-contract message changes.

## TON testnet escrow v1

**Status:** testnet implementation; not deployed; no production address.

### Contract identity
- One escrow instance per marketplace deal.
- Immutable deal ID, seller address, buyer address, NFT item address, payment asset, expected amount, platform fee, expiry and dispute authorities.
- Network is testnet only for this implementation.

### Planned message surface
- `DeployEscrow`: initialize a deal with immutable participant and asset data.
- TEP-62 `OwnershipAssigned`: accept custody only from the configured NFT item and only when previous owner is the configured seller.
- `RecordStarsPayment`: backend-authorized payment attestation after Telegram `successful_payment`; idempotent by Telegram charge ID and exact deal amount. Does not claim Stars are held on-chain.
- `ReleaseNftToBuyer`: request NFT transfer after payment is verified.
- `ConfirmDelivery`: backend-oracle attestation after TON provider quorum independently verifies buyer ownership.
- `RefundDeal`: refund/return flow only when evidence proves it is safe.
- `RaiseDispute` / `ApproveDisputeResolution`: pause normal settlement and apply the configured multi-approver resolution policy.
- Getters expose state, parties, asset, amount, deadline, payment evidence reference and transfer status.

These are design names. Exact Tact-generated opcodes and serialized layouts are frozen only after contract tests and ABI generation.

### API policy
- No new public API route is considered stable until its request/response schema is documented here.
- No endpoint accepts a wallet private key, seed phrase or signing secret.
- TON proof challenges are one-time, short-lived and bound to the authenticated Telegram session.
