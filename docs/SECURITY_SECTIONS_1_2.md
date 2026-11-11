# GIFT RADAR security design — Sections 1–2

This document separates the **target security architecture** from the **currently deployed capabilities**. Designing a payment flow does not mean that real payments are enabled.

## 1. Telegram authentication and session security

### Threats
- Forged or modified Telegram Mini App identity data.
- Stale or replayed `initData`.
- Session theft, session reuse after logout and account takeover.
- Client-side role escalation and insecure direct-object references.
- Credential leakage through browser storage, logs or error reports.

### Required controls
1. Verify Telegram's documented HMAC signature on the server using the bot token held in a secret manager.
2. Reject duplicate/empty parameters, malformed hashes, oversized payloads and invalid user objects.
3. Enforce a short `auth_date` freshness window and reject timestamps materially in the future.
4. Atomically consume each signed initData hash once; canonicalize replay identity independently of query-string parameter order.
5. Exchange initData for a cryptographically random opaque session. Persist only its SHA-256 digest, not the bearer token.
6. Require the server-issued session on every private API route. Never accept raw initData as an API credential.
7. Enforce account ownership in each database query/mutation; never trust client-supplied user IDs or role flags.
8. Support listing safe session metadata, revoking the current session and revoking all sessions for an account.
9. Keep admin authorization server-side and independent from frontend visibility.
10. Do not log initData, session tokens, bot tokens or webhook secrets.

### Example: session exchange
```python
# Pseudocode: actual implementation lives in app.py / marketplace.py
user = verify_telegram_init_data(raw_init_data)
if not consume_init_data_once(raw_init_data, user["id"]):
    reject_replay()
token = create_random_session_token()
store(sha256(token), user["id"], expires_in=12 * 60 * 60)
return token
```

### Example: API authorization
```python
def authenticate(session_token: str):
    if not session_token.startswith("grs_"):
        raise Unauthorized()
    user = lookup_by_hash(sha256(session_token))
    if not user or user.revoked or user.expires_at <= now():
        raise Unauthorized()
    return user
```

### Operational controls
- Rotate the Telegram bot token immediately if it is exposed.
- Store secrets only in Railway Variables or an approved secret manager.
- Use HTTPS end-to-end; restrict CORS to known Mini App origins.
- Alert on repeated invalid signatures, replay attempts, unusual session creation and repeated 401/403 responses.
- Use persistent storage and backups for the session database; SQLite files on ephemeral storage are not durable.
- Keep session lifetime short. A later iteration may add explicit device labels and trusted-device management, but must not fingerprint users covertly.

### Current implementation
- Signed initData validation, freshness and replay protection.
- Opaque 12-hour session tokens with digest-only persistence.
- Session list, current-session logout and account-wide logout.
- Private API routes use server-side session resolution.
- Session metadata never includes the token or token digest.

## 2. Real marketplace payment and settlement security

### Product boundary

GIFT RADAR is intended to become a real marketplace, not remain a simulated order book. The current deployed order/offer flow is still demonstration-only; no live invoice, blockchain settlement, asset transfer or seller payout is enabled yet.

The marketplace must not obtain custody or discretionary control over a user's assets. Specifically, it must never request or store:
- seed phrases, private keys or wallet passwords;
- exchange/account API keys or credentials;
- bank-card credentials or banking login details;
- permission to debit, credit, freeze or otherwise mutate a user's wallet/account balance.

A wallet connection is optional and user-initiated. The application may receive a public wallet address and request a transaction for the user to review and approve in their own wallet. The wallet signs; GIFT RADAR does not. A public address is not a private key, but it is still personal data and must be collected only when necessary, with clear disclosure and access controls.

### 2.1 Telegram Stars — server-authoritative payment

For digital goods or services sold inside Telegram, use Telegram Stars (`XTR`) in accordance with Telegram's current platform rules. Do not silently substitute TON for a Stars-required in-app digital purchase.

1. The authenticated buyer selects an eligible marketplace order. The server loads the order, seller, exact price, currency and current state from its own database. It never trusts a client-supplied amount, seller ID or payment status.
2. The server creates an opaque, random, single-purpose invoice payload bound to that order, buyer and expiry. Store only the necessary payload digest/reference and authoritative amount. The Telegram invoice is created server-side in `XTR`; the Stars provider token is empty.
3. On `pre_checkout_query`, validate the Telegram buyer ID, payload, exact amount, currency, expiry, order state and absence of an existing settlement. Respond within Telegram's deadline. A successful pre-checkout response is **not** proof of payment.
4. Only a verified Telegram `successful_payment` update received through the authenticated webhook (or trusted Bot API polling) is payment evidence.
5. In one database transaction, validate the invoice/order binding again, insert a payment transaction with unique Telegram charge ID and source update ID, and perform a compare-and-set order transition. Duplicate events must be harmless; conflicting reuse must be rejected and audited.
6. Fulfilment is a separate state transition. A payment confirmation alone must not falsely mark an NFT/gift as delivered.
7. Refunds must be initiated through the supported Telegram refund mechanism using the original charge reference, with authorization, idempotency, audit history and reconciliation. A browser request is never proof that a refund happened.

**Important platform settlement fact:** Telegram Stars are paid to the bot/platform's Telegram Stars balance. This is not a direct peer-to-peer seller payout. Therefore the product must explicitly define the legal/operator role, seller accounting, refund responsibility, payout method, timing, fees and dispute handling before production. The owner's commercial profit, commission rate and revenue split are intentionally **TBD pending agreement**; no percentage or payout promise is hard-coded.

### 2.2 TON — non-custodial wallet-approved settlement

TON Connect may be used for optional wallet connection and user-approved transactions:
- The browser receives the wallet connection/session through the official SDK; backend storage is limited to the minimum public identity/proof data required.
- The user reviews and approves each transaction in their own wallet. No server-held private key, seed phrase, signing authority or background transfer is permitted.
- The backend must independently verify the resulting transaction on-chain: network/chain, destination or contract, sender, amount, asset, order reference, transaction hash, confirmation/finality and replay status. Never trust a frontend callback or a pasted transaction hash by itself.
- A wallet address must be proven to belong to the connected user when account binding is required. Use a short-lived, single-use server challenge and verify the official TON proof format, domain, timestamp and signature.
- Direct buyer-to-seller transfer is non-custodial but does not guarantee delivery of the NFT/gift. Payment and asset delivery can fail independently. Model them separately and expose a pending/dispute state rather than claiming atomic settlement.
- Any smart-contract escrow or atomic swap is a separate product/security scope: use an independently audited, narrowly scoped contract, disclose its rules and risks, and never describe an unaudited custom contract as safe.

TON must not be presented as a workaround for Telegram's Stars rules for digital goods sold inside Telegram. Product/legal review must determine which blockchain transactions are allowed in each surface and jurisdiction.

### 2.3 Order, payment and delivery state machine

Keep order, payment and asset-delivery state distinct. A proposed lifecycle is:

- Order: `active → reserved → payment_pending → paid → fulfilment_pending → completed`; terminal alternatives include `cancelled`, `expired`, `disputed` and `refunded`.
- Payment: `created → pending → confirmed`; alternatives include `failed`, `expired`, `refund_pending`, `refunded` and `reconciliation_required`.
- Asset delivery: `not_started → transfer_pending → transfer_confirmed`; alternatives include `failed` and `disputed`.

Transitions must be explicit, authenticated, atomic where possible, idempotent and recorded in an append-only audit/event history. Never use a mutable user-facing “balance” as a substitute for a payment ledger. Any seller payable/settlement record must be clearly identified as an accounting liability, not a wallet balance and not freely withdrawable until an approved payout rail and policy exist.

### 2.4 Webhook and replay controls already implemented

- Telegram webhook secret-token header checked using constant-time comparison.
- Request body capped at 1 MiB.
- Durable inbox keyed by Telegram `update_id`.
- Canonical payload digest detects reuse of an update ID with different content.
- Processed updates are not dispatched twice.
- A short processing lease prevents concurrent duplicate dispatch; Telegram receives a retryable 503 while an update is actively being processed.
- Failed dispatch releases the lease for retry.
- Processed inbox rows are pruned after 14 days.

This inbox is transport-level idempotency only. It is not yet a payment ledger and does not itself prove that money moved.

### 2.5 Implementation gates before real money is enabled

- Dedicated order/payment/asset-delivery schemas and migration/backups.
- Server-only invoice creation and strict pre-checkout validation.
- Unique payment charge, invoice payload and source-event constraints; race-safe state transitions.
- Authenticated successful-payment handler, refund flow, reconciliation and audit log.
- TON Connect integration, proof verification, trusted on-chain transaction verification and explicit network configuration.
- Delivery verification and dispute handling; no “paid = delivered” shortcut.
- Provider/platform approval, product/legal review, seller terms, fee/payout agreement and production support process.
- Automated tests for forged events, wrong buyer/amount/currency/order, expired invoices, duplicate charge IDs, webhook replay, concurrency, refunds, chain reorgs and failed asset delivery.
- Feature flags remain off until production configuration, approvals, monitoring, backups and end-to-end tests are complete.

### Current implementation status (verified in repository)

- Telegram authentication/session controls and durable webhook inbox are implemented.
- Marketplace orders/offers are still simulated.
- No real Stars invoice, pre-checkout handler, successful-payment ledger, refund execution, TON Connect wallet flow, on-chain verifier, escrow or seller payout is implemented.
- The security architecture above is the target for the next implementation stages, not a claim that payments are live.

### Non-negotiable user-asset boundary

GIFT RADAR must never ask for or store a user's seed phrase, private key, wallet password, exchange API key or account API token. It must not sign transactions on a user's behalf or custody user assets. The only wallet actions are explicit, user-approved actions through the user's wallet, with server-side verification of public chain results.
