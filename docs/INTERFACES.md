# Public Interfaces

This file records externally observable API and on-chain interfaces. Update it in the same pull request whenever a public route, request/response schema, webhook contract or smart-contract message changes.

## TON testnet escrow v1

**Status:** testnet implementation; not deployed; no production address.

### Contract identity
- One escrow instance per marketplace deal.
- Immutable deal ID, seller address, buyer address, NFT item address, payment asset, expected amount, platform fee, expiry and dispute authorities.
- Network is testnet only for this implementation.

### Implemented message surface

The contract source is `contracts/GiftRadarEscrow.tolk`. The following 32-bit opcodes are now fixed in the testnet source:

| Message | Opcode | Sender / guard |
|---|---:|---|
| `DeployEscrow` | `0x47524401` | Payment authority; one-time configuration validation |
| TEP-62 `NotificationForNewOwner` | `0x05138d91` | Configured NFT item only; previous owner must be seller |
| `RecordStarsPayment` | `0x47524402` | Payment authority; exact XTR amount; charge hash idempotency |
| `RequestNftRelease` | `0x47524403` | Buyer only; state must be paid |
| `ConfirmNftDelivery` | `0x47524404` | Payment authority after independent buyer-ownership proof |
| `MarkSellerSettled` | `0x47524405` | Payment authority after payout reference exists |
| `RequestRefund` | `0x47524406` | Payment authority; expired unpaid deal or confirmed Telegram refund |
| `ConfirmNftReturned` | `0x47524407` | Payment authority after independent seller-ownership proof |
| `RaiseDispute` | `0x47524408` | Buyer or seller; transfer-pending disputes go to reconciliation |
| `RecordStarsRefund` | `0x47524409` | Payment authority after Telegram refund succeeds |
| `RecordNoPaymentEvidence` | `0x4752440b` | Payment authority after Telegram Stars transaction-history reconciliation for an unpaid deal |
| `ApproveDisputeResolution` | `0x4752440a` | Two distinct configured arbitrators must approve the same decision and hash; refund needs payment-refund or no-payment evidence |

Outbound NFT transfers use the TEP-62 `transfer` opcode `0x5fcc3d14`. A bounced NFT transfer moves the escrow to `reconciliation_required`; it is never retried or settled automatically.

### State values

| Value | State |
|---:|---|
| 0 | `created` |
| 1 | `deposited` |
| 2 | `paid` |
| 3 | `transfer_pending` |
| 4 | `delivered` |
| 5 | `settled` |
| 6 | `refunded` |
| 7 | `disputed` |
| 8 | `reconciliation_required` |
| 9 | `refund_pending` |

### Getters
- `get_escrow_state`
- `get_escrow_parties`
- `get_escrow_authorities`
- `get_payment_evidence`
- `get_delivery_evidence`

The contract records a Telegram Stars payment attestation but does not custody Stars or provide a Telegram seller payout primitive. Backend Ledger settlement remains a separate system and must be reconciled. This testnet v1 has no TON/Jetton payment receiver.

### API policy
- No new public API route is considered stable until its request/response schema is documented here.
- No endpoint accepts a wallet private key, seed phrase or signing secret.
- TON proof challenges are one-time, short-lived and bound to the authenticated Telegram session.
