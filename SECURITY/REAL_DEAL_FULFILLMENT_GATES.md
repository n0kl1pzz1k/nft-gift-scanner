# Real Deal Fulfilment Gates

Status: implementation foundation only. Refund request and Telegram-confirmed refund execution workflows exist in code, but production payments, live deals, transfers, escrow and seller payouts remain disabled.

## 1. Non-custodial boundary

GIFT RADAR must never request or store seed phrases, private keys, wallet passwords, Telegram login codes, or exchange API credentials. TON Proof proves control of a wallet address; it does not prove that a gift is transferable, that a listing is valid, or that the seller will deliver.

The marketplace may store public identifiers, signed proof results, order metadata, and immutable payment/audit references. It must not operate a user's wallet or edit a user's balance.

## 2. Evidence required before a deal can be marked real

A deal may be created with `is_real=1` only after all applicable evidence is available and independently validated:

1. **Canonical asset identity:** chain/network, canonical collection or contract address, token/item identifier, and asset standard. Display names and images are never identity.
2. **Current ownership:** authoritative chain/indexer or official Telegram ownership evidence, with a timestamp and source reference. A screenshot, client claim, or Portals catalogue entry alone is insufficient.
3. **Seller control:** a verified wallet proof bound to the authenticated Telegram user, or an equally strong official Telegram account proof for non-chain gifts.
4. **Transferability:** current asset state and platform rules allow the intended transfer. Frozen, burned, revoked, non-transferable, or unsupported assets fail closed.
5. **Price integrity:** server-side integer amount, currency policy, fee schedule, and immutable quote expiry. Client-provided price is never authoritative.
6. **Buyer confirmation:** authenticated buyer, current versioned terms acceptance, and explicit confirmation of the exact item and total.

Ownership proof is a snapshot, not a reservation. Before payment approval, revalidate ownership and listing state; use a short reservation lease and reject stale evidence.

## 3. Delivery and settlement decision

The product must choose and independently review one of these models before enabling real deals:

- **Atomic on-chain escrow:** only if a reviewed contract can hold and release the exact asset and payment atomically, with audited contract code and tested recovery paths.
- **Non-custodial direct transfer:** seller transfers through their own wallet or official Telegram UI. This is not atomic with Telegram Stars. The product must explicitly document the buyer/seller risk and must not claim escrow or guaranteed delivery.
- **Trusted external marketplace fulfilment:** only with a documented, authorized partner API and verifiable settlement callbacks. Read-only catalogue access is not fulfilment authority.

If none of these models is implemented and independently tested, `MARKETPLACE_REAL_DEALS_ENABLED` stays false and all deals remain `is_real=0`. A Stars receipt alone is not proof of delivery.

## 4. Payment state machine

Allowed progression:

`matched -> awaiting_payment -> pre_checkout_approved -> paid -> delivery_pending -> completed`

Exceptional transitions must be explicit and audited:

- `awaiting_payment -> expired` when the server-owned invoice expires.
- `paid -> disputed` only through a documented support/dispute process.
- `paid|delivery_pending -> refunded` only after Telegram confirms a successful Stars refund and the ledger reversal commits.
- `matched|awaiting_payment -> cancelled` only if no successful payment exists.

Never mark a deal paid from a client callback, browser redirect, invoice-link click, screenshot, or pre-checkout approval. Only Telegram's authenticated `successful_payment` update is payment evidence. Persist `telegram_payment_charge_id` and make duplicate updates harmless.

## 5. Refund and seller liability controls

- A buyer's `/paysupport` request creates an auditable support case; it does not itself issue a refund.
- Refund execution must use Telegram's official `refundStarPayment` method with the buyer's Telegram user ID and the stored `telegram_payment_charge_id`.
- The buyer refund-request endpoint creates a durable request; an authenticated administrator explicitly claims and executes it with Telegram's `refundStarPayment`. The refund workflow records request, operator, reason, confirmed success/failure and a response reference. A timeout or ambiguous API response remains in `processing` and must be reconciled against Telegram transaction history before any retry.
- On confirmed refund, post a compensating double-entry journal entry. Never edit or delete the original payment journal.
- Seller payouts are a separate liability-settlement workflow. No payout may be implied by a `paid` deal state. Do not enable seller payouts until the legal, operational, and supported Telegram Stars withdrawal model is documented and independently tested.


## Listing cancellation and payment amount guards

- A seller may cancel only an active listing that has not been reserved. A reserved listing cannot be withdrawn through the seller endpoint.
- Cancelling a listing does not move the NFT out of escrow. Asset return is a separate, explicitly authorized and independently verified operation; the UI must say so.
- Crypto invoice creation applies base-unit anti-dust floors: 0.01 native TON and 0.10 TON-USDT. The floors are policy safeguards and do not enable either rail.
- The Mini App may read `GET /api/payments/capabilities` to render server-owned availability. A client cannot override disabled server-side payment policy.
- Public inventory listing search supports bounded pagination and filtering, and returns the full filtered count rather than the current page length.

## 6. Activation gates

All of the following are mandatory before any production real-deal activation:

- PostgreSQL migration and data reconciliation completed with verified backup and rollback.
- Real inventory proof and revalidation adapter tested against authoritative sources.
- Native NFT escrow monitoring remains inert unless `NATIVE_NFT_ESCROW_MONITOR_ENABLED=true`, PostgreSQL is active, both independent TON NFT providers are configured, and written Kazakhstan legal, data-residency, tax and security approvals are recorded.
- Delivery/escrow model selected, documented, and independently reviewed.
- Versioned HTTPS terms and support route published; buyer acceptance recorded before invoice creation.
- Telegram Stars end-to-end test including pre-checkout timeout, duplicate updates, failed fulfilment, confirmed refund, ambiguous API timeout, and reconciliation.
- Load, concurrency, rate-limit, backup/restore, incident-response, and security testing completed.
- Separate explicit release approval after staging evidence is reviewed.

Until then, `TELEGRAM_STARS_PAYMENTS_ENABLED` and `MARKETPLACE_REAL_DEALS_ENABLED` remain false in production. No production deployment, database cutover, wallet transaction, or payment operation is authorized by this document.


## Unpaid deal expiry

- A reserved deal expires when its linked buyer reservation expires, provided no payment has been reserved into the deal hold. Buyers cannot cancel a reservation after a deal has been created, and payment reservation rechecks that the reservation is still active and unexpired.
- Expiry is processed in bounded batches by the application worker and is idempotent.
- Only deals still in `reserved` state are eligible. `payment_confirmed`, `nft_transfer_pending`, disputed, settled, and reconciliation-required deals are never expired by this worker.
- A listing is reactivated only when the NFT remains in escrow and its ownership proof is still fresh. Otherwise the listing returns to `pending_verification` and must pass the independent escrow monitor again before publication.
- Buyer cancellation and administrator refund use the same escrow-proof freshness rule. A buyer cannot cancel after deal creation, and payment reservation independently rejects a cancelled or expired reservation.
- PostgreSQL deployments must apply Alembic revision `0012_expire_unpaid_deals` or a later revision before settlement endpoints run.
- Railway's pre-deploy command applies `alembic upgrade head`; deployment must still be reviewed and verified separately before production use.


## Buyer and seller disputes

- Only the authenticated buyer or seller on a deal may open a dispute.
- A dispute may be opened once per deal. Repeated requests are idempotent and do not create duplicate dispute events.
- Before NFT transfer starts, a funded deal enters `disputed`; its funds remain held and no automatic seller payout occurs.
- If NFT transfer is already pending, a dispute moves the deal to `reconciliation_required`. This deliberately blocks automatic refund or payout because the chain transfer may already have been broadcast.
- Dispute reasons and state transitions are stored in the deal event journal and are visible only to the authenticated buyer and seller through the deal detail endpoint.
- This is a dispute intake and safety gate, not a complete human arbitration operation. An authorized, audited admin resolution workflow and evidence review remain required before funds can be released.


## Administrative dispute decisions

- The admin resolution endpoint is restricted to Telegram users listed in `ADMIN_TELEGRAM_IDS` / `ADMIN_TELEGRAM_ID` and requires a server-issued session.
- An admin may dismiss a dispute only while the deal is still in the pre-transfer `disputed` state. Dismissal returns it to `payment_confirmed` and leaves funds in the deal hold; it does not pay the seller.
- An admin refund decision is restricted to the `disputed` state and rechecks that state under the deal row lock before posting the refund journal.
- Telegram Stars dispute refunds restore the accounting hold and explicitly return `manual_telegram_refund`; an admin must still complete the official Telegram refund flow and wait for its confirmed result before recording the final refund.
- Deals in `reconciliation_required` cannot be dismissed or refunded by this workflow. They require independent chain evidence and a separately reviewed manual reconciliation process.
- This workflow records a decision and reason, not uploaded evidence or a full appeals/arbitration system.


## Late TON deposit handling

- Invoices remain monitored for a bounded 24-hour grace window after their displayed expiry. This catches transfers that were sent before or around expiry but indexed late.
- If both independent TON providers confirm an exact matching transfer after invoice expiry, the invoice moves to `reconciliation_required`. No user balance is credited automatically, and repeat observations remain quarantined idempotently.
- Expired invoices older than the grace window stop consuming provider polling capacity. Admins can inspect the bounded reconciliation queue at `GET /api/admin/payments/crypto/reconciliation`; invoice owners see the review-required state from the invoice status endpoint.

## TON GRAM and USDT deposit checkout

- Buyers fund an existing reserved deal with `POST /api/inventory/deals/{deal_id}/pay-from-invoice`. The server checks Telegram identity against the deal buyer, verifies a paid chain invoice for the exact asset and amount, and moves the funds into the deal hold atomically. A client cannot pay another user's deal or choose the hold amount.


- Authenticated buyers can request a server-owned TON deposit invoice at `POST /api/payments/crypto/invoices`; the client supplies only the allowlisted asset ID and an integer base-unit amount. The server chooses the deposit address, token master, unique invoice payload and expiry.
- `GET /api/payments/crypto/invoices/{invoice_id}` returns status only to the invoice owner and exposes the confirmed chain event when one exists.
- GRAM (native TON coin) and the allowlisted USDT TON jetton are eligible assets, but checkout remains fail-closed until the crypto-deposit monitor, dual providers, PostgreSQL, the configured deposit address and all Kazakhstan legal/data/tax/security gates are present. USDT on TRON remains unsupported by this Mini App payment rail.
- The deposit API does not accept client-supplied recipient addresses, token master contracts, transaction hashes or proof screenshots. The client cannot directly credit balances; only the independent provider quorum and the ledger settlement gate can do so.
- This endpoint does not activate seller withdrawals or NFT escrow settlement and does not change production configuration.

## Native marketplace Telegram Stars checkout

- Native marketplace deals (`grd_…`) now use the same server-owned XTR invoice, pre-checkout validation and authenticated `successful_payment` receipt flow as the existing Stars checkout.
- Invoice creation is restricted to the authenticated buyer of an active, unexpired reservation for an XTR-priced listing. Pre-checkout rechecks the deal, reservation, listing, buyer and exact integer Stars amount.
- The successful payment update atomically appends the Stars receipt, posts the balanced ledger entry into that native deal's hold, changes the deal to `payment_confirmed`, and appends a deal event. A duplicate Telegram charge cannot credit the hold twice.
- A confirmed Telegram Stars refund reverses the original hold only after Telegram's official refund call confirms success. For native deals, the listing is moved to `pending_verification` after refund rather than being silently republished; custody must be rechecked before relisting.
- Refund requests and execution are rejected once native NFT transfer has started or the deal is otherwise in reconciliation. Ambiguous Telegram refund responses remain quarantined and cannot be blindly retried.
- Native checkout is exposed at `POST /api/inventory/deals/{deal_id}/stars-invoice`. The existing legacy Stars endpoint remains separate and backwards compatible.
- These code paths do not enable production checkout. The existing Stars release gates, legal/tax/data-residency/security approvals, PostgreSQL requirement and explicit production authorization remain mandatory.

## Manual chain reconciliation queue

- Admins can inspect the bounded, oldest-first queue at `GET /api/admin/inventory/reconciliation`; access requires the normal Telegram admin session and configured admin allowlist.
- Queue entries include deal/listing/asset state, buyer and seller account references, payment amount/currency, transfer transaction reference when present, and the chronological deal event journal (capped at the latest 25 events per deal).
- Admins may append a bounded operational note with `POST /api/admin/inventory/deals/{deal_id}/reconciliation-notes`. The note is stored as an append-only deal event; the idempotency key prevents duplicate notes on retries.
- Adding a note does not change deal status, ledger balances, listing status, custody state, or wallet state. The endpoint deliberately provides no "mark resolved", refund, payout, or release action.
- Queue pagination is bounded to 100 rows. The status/update and deal-event indexes are introduced by Alembic revision `0013_reconciliation_queue`; the asset/source proof-history index used to fetch the latest seller and escrow evidence independently is introduced by `0014_ownership_proof_index`.
- This is an auditable work queue, not a chain adjudication engine. Resolution still requires independent provider evidence and a separately reviewed, explicitly authorized operation. No ambiguous transfer is auto-settled.
