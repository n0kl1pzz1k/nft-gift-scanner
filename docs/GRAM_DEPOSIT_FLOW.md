# GRAM deposits

This implementation adds a server-verified GRAM deposit path. It does not enable production deposits by itself.

## Request flow

1. The authenticated Mini App calls POST /api/payments/gram/deposits with amount_gram and an idempotency key.
2. The backend creates a user-scoped deposit intent with an exact integer nano-GRAM amount, a random one-time HASSLE: memo, treasury address, network and expiry.
3. The response contains a TON Connect transaction request. The Mini App asks the user's wallet to send the exact amount and comment.
4. The backend indexer polls the treasury account's TONAPI transaction history. It does not accept a client transaction hash or wallet callback as proof of payment.
5. A transaction is eligible only when TONAPI reports it successful and not aborted/bounced, the incoming destination matches the configured treasury address, the exact nano amount and memo match a live intent, and the chain timestamp falls inside the intent's lifetime.
6. In one database transaction, the backend stores a unique chain event, posts a balanced double-entry ledger transaction, and marks the intent confirmed. The chain identity is (network, transaction hash, logical time).
7. Repeated polling cannot credit the same event twice. Expired intents remain eligible for reconciliation when the transfer itself was included before the intent expiry.

## API

- POST /api/payments/gram/deposits
  - Auth: server-issued Telegram session in X-Telegram-Init-Data.
  - Body: amount_gram as a decimal string and idempotency_key as a client-generated UUID.
  - Returns the deposit ID, treasury address, exact nano amount, memo, expiry and TON Connect transaction object.
- GET /api/payments/gram/deposits/{deposit_id}
  - Returns status only to the owning Telegram user.
- GET /api/payments/gram/deposits/active
  - Restores the user's current unexpired deposit request after a Mini App reload.

The client must never credit a balance after sendTransaction() resolves. That only means the wallet submitted a request; the server indexer is authoritative.

## Runtime gates

The indexer and API require all of the following:

- HASSLE_TON_CHECKOUT_ENABLED=1
- HASSLE_TON_TREASURY_ADDRESS set to the actual treasury address
- HASSLE_TON_NETWORK set to mainnet or testnet (defaults to mainnet only when the feature gate is explicitly enabled)
- TONAPI_TOKEN set in the backend secret store

Do not enable the gate until the treasury address and network have been independently verified. The implementation does not add or expose a private key, seed phrase, hot-wallet signer or automatic withdrawal path.

## Storage

gram_deposit_intents stores the one-time request and its lifecycle. gram_deposit_chain_events stores chain observations and has uniqueness constraints on the chain event and deposit. The existing marketplace_ledger_transactions / marketplace_ledger_entries tables are reused for double-entry crediting in the existing chain currency column (TON); user-facing API and Mini App labels remain GRAM.

## Operational limitations

- The poller reads up to 20 pages of 100 treasury transactions per cycle and reconciles recent expired intents for 24 hours. If the provider is unavailable for longer, use a controlled reconciliation process; never credit from a user-submitted hash.
- Provider account history is used as the source of successful canonical account transactions. A separate configurable multi-confirmation depth has not yet been implemented.
- Production deposit activation, multisig withdrawals, NFT escrow custody and off-chain Telegram asset custody are separate release gates.
- This change does not deploy to production or modify Railway environment variables.
