# Public API and Mini App Interface Change Log

This file records externally observable API and Mini App behavior changes. Internal refactors that preserve request/response behavior are not listed.

## 2026-11-14 — Phase 0 testnet-only hardening (PR #52)

**Compatibility intent:** fail closed for mainnet operations while the project is in testnet-only development. No production money movement is supported.

### Wallet proof and network

- `POST /api/wallet/ton-proof/verify`: `network` now accepts only TON testnet chain ID `-3`. A mainnet chain ID `-239` returns HTTP 422.
- TON Proof service verification schema accepts only `-3`; its wallet public-key lookup uses the testnet RPC endpoint.
- TON Connect UI now checks the connected wallet's `account.chain` and refuses to continue unless it is `CHAIN.TESTNET`. The user may need to switch the wallet app to testnet and reconnect.
- `GET /api/wallet/policy`: `supported_network` is now `ton-testnet`.

### NFT inventory and custody

- `POST /api/inventory/nft/import` returns HTTP 503 until a testnet NFT ownership verifier exists.
- `POST /api/inventory/listings` returns HTTP 503 until testnet ownership and custody verification exist.
- The native NFT escrow monitor remains disabled. These endpoints must not fall back to the mainnet-only ownership adapters.

### Payment rails

- `POST /api/payments/crypto/invoices` and `POST /api/wallet/withdrawals` remain unavailable because the current TON deposit and payout adapters are mainnet-specific. Legacy environment flags do not override the code-level release policy.
- `GET /api/payments/capabilities` reports the unavailable rails as disabled.
- Telegram Stars invoice/pre-checkout is disabled until an isolated Telegram test payment environment is implemented. The successful-payment bot handler does not post an unexpected payment update to the ledger while the test environment is disabled.
- `POST /api/admin/payments/stars/refunds/{request_id}/execute` returns HTTP 503 while the isolated test payment environment is unavailable.
- The TON broadcaster refuses direct mainnet sends while the testnet withdrawal adapter is unavailable.

### API protection behavior

- All `/api/*` paths are subject to Redis-backed per-IP rate limiting. Current budgets: 120 requests/minute for reads, 60/minute for most mutations, 300/minute for marketplace mutations (which also have domain-level per-account quotas), and 300/minute for the Telegram webhook.
- Production API traffic fails closed if Redis is not configured or unavailable. Development uses a process-local limiter.
- POST/PUT/PATCH/DELETE API request bodies are limited to 1 MiB, including chunked bodies without a Content-Length header. The TON Proof service retains its stricter 16 KiB limit.
- Rate-limit responses return HTTP 429 with `Retry-After`; oversized bodies return HTTP 413.
- JSON API responses, including middleware-generated errors, receive `Cache-Control: no-store`. Allowed CORS origins also receive CORS headers on inner middleware rejection responses.

No database schema migration is required for these interface changes. These restrictions are temporary safety behavior and may be relaxed only after testnet-specific adapters, tests and the release checklist are approved.
