# GIFT RADAR security design — Sections 1–2

This document describes the security baseline and the deliberately disabled payment architecture. It is not a claim that real payments, custody or transfers are enabled.

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

## 2. Telegram Stars payment security — design only, disabled

### Current state
**No invoices are created, no Stars are charged, no payment is accepted as order settlement, and no user balance is modified.** The current marketplace remains simulation-only.

### Threats
- Forged client-side “payment successful” callbacks or screenshots.
- Amount/currency substitution and payment against a different order.
- Replayed Telegram updates or duplicate charge processing.
- Webhook spoofing, oversized payloads and denial of service.
- Race conditions where two updates attempt to settle one order.
- Treating an invoice being opened or pre-checkout being approved as proof of payment.

### Future Telegram Stars flow (must remain disabled until separately reviewed)
1. Server loads an existing eligible order and its authoritative price from the database. Never trust price, currency, seller or user ID supplied by the browser.
2. Server creates a Telegram invoice using currency `XTR`, a unique opaque invoice payload and the server-owned amount. Stars invoices use an empty provider token.
3. On `pre_checkout_query`, verify Telegram user ID, invoice payload, order state, exact amount, currency and expiry. Reject mismatches and already-settled orders. Respond within Telegram's required time limit.
4. Treat pre-checkout approval as permission to attempt payment, **not** as payment evidence.
5. Only a Telegram `successful_payment` update received through the authenticated webhook (or trusted Bot API polling) may be considered payment evidence.
6. In one database transaction, insert a payment record with unique Telegram charge ID and update ID, validate the order is still payable, and transition it exactly once.
7. Duplicate update IDs and charge IDs must be harmless. A conflicting payload for an already-seen update ID must be rejected and investigated.
8. Refunds, disputes and fulfillment require separate state transitions, audit records and tests. Never infer a refund from a client request.

### Webhook controls already implemented
- Telegram webhook secret-token header checked using constant-time comparison.
- Request body capped at 1 MiB.
- Durable inbox keyed by Telegram `update_id`.
- Canonical payload digest detects reuse of an update ID with different content.
- Processed updates are not dispatched twice.
- A short processing lease prevents concurrent duplicate dispatch; Telegram receives a retryable 503 while an update is actively being processed.
- Failed dispatch releases the lease for retry.
- Processed inbox rows are pruned after 14 days.

### What must be added before enabling real Stars payments
- Dedicated invoice/order schema and server-only invoice creation.
- Strict pre-checkout validation with race-safe database transaction.
- Payment ledger with unique `telegram_payment_charge_id`, unique invoice payload and unique source update ID.
- Authenticated successful-payment handler and exact amount/currency/order/user binding.
- Idempotent order state machine, refunds and reconciliation.
- Integration tests using Telegram test fixtures, failure injection and concurrency tests.
- Security review and explicit product approval before turning the feature flag on.

### Non-negotiable user-asset boundary
GIFT RADAR must never ask for or store a user's seed phrase, private key, wallet password, exchange API key or account API token. It must not debit/credit a user balance, sign transactions on a user's behalf, or custody assets. Any future wallet connection should be optional, user-initiated, scoped to public-address verification, and must not grant transfer authority.
