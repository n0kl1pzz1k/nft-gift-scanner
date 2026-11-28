# GRAM deposits

GRAM deposits use TON Connect for the user's wallet signature and TONAPI for
server-side verification. The browser never confirms a payment and never
submits a transaction hash as proof of payment.

## Runtime configuration

Required server-side variables:

- \`HASSLE_GRAM_DEPOSITS_ENABLED=1\` to expose deposit creation and run polling.
  It is intentionally off unless an operator explicitly enables it.
- \`HASSLE_TON_NETWORK=mainnet\` (or \`testnet\` for isolated testing).
- \`HASSLE_TON_TREASURY_ADDRESS\` must be the exact treasury address for that network.
- \`TONAPI_TOKEN\` must be a server-side secret.

The app polls TONAPI every 15 seconds by default. Override with
\`HASSLE_GRAM_INDEXER_INTERVAL_SECONDS\` (minimum 5 seconds). Mainnet and testnet
use their matching TONAPI hosts.

## API

All endpoints require the existing server-issued Telegram session in
\`X-Telegram-Init-Data\`.

- \`POST /api/gram/deposits\` with \`{"amount":"1.25"}\`: create a unique deposit
  intent and return a TON Connect transaction proposal.
- \`GET /api/gram/deposits/{id}\`: read only the caller's deposit state.
- \`GET /api/gram/balance\`: read the caller's GRAM balance in nano and display units.

The transaction proposal contains the treasury destination, exact nano amount,
and a unique standard TON text-comment payload. The UI checks the connected
wallet's network before asking it to sign.

## Credit conditions

The indexer only considers successful native \`TonTransfer\` actions in completed
TONAPI events. It matches the configured treasury account and exact recipient, exact nano amount, unique
comment, configured network, explicit non-scam status, and completed event. Before issuing a
request, the API also verifies through TONAPI that the treasury account is active. It then
uses one database transaction to:

1. insert a unique chain event;
2. post the balanced credit into the existing marketplace ledger
   (\`user_balance\`, internal currency \`TON\`, displayed as GRAM);
3. mark the deposit intent confirmed.

The chain-event and ledger idempotency keys make retries safe. Client callbacks
are only UX signals and cannot credit a balance. The TONAPI event identifier is
stored as the chain event reference; never trust a client-supplied hash.

## Safety gates and remaining work

- Deposits are disabled until the operator explicitly enables the feature flag
  after validating the treasury address and network.
- Payouts remain disabled. This change does not add a signer, private key,
  multisig deployment, or automatic withdrawal execution.
- On-chain NFT escrow and Telegram off-chain gift custody are separate flows
  and are not activated by this deposit implementation.
- Before enabling mainnet deposits, run the integration suite against a
  controlled testnet wallet and verify TONAPI's event response for the
  configured treasury.


## GRAM withdrawal queue

The withdrawal API is a separate, fail-closed flow:

- POST /api/gram/withdrawals creates a request using an idempotency key.
- GET /api/gram/withdrawals and GET /api/gram/withdrawals/{id} expose only the authenticated user's requests.
- POST /api/gram/withdrawals/{id}/cancel releases the reserve only while the request is still queued_for_multisig.

Before a request can be created, the operator must explicitly set both HASSLE_GRAM_WITHDRAWALS_ENABLED=1 and HASSLE_GRAM_WITHDRAWAL_EXECUTOR_ENABLED=1, acknowledge custody, configure manual_multisig_2_of_3, and the backend must verify that the configured treasury matches an active on-chain 2-of-3 multisig. Both feature flags default to off. The executor flag must remain off until signer/order approval and payout verification are implemented.

The backend serializes requests by Telegram user, checks the canonical ledger balance, daily and per-request limits, and reserves the gross amount with a balanced ledger transaction. The configurable fee is HASSLE_GRAM_WITHDRAWAL_FEE_BPS (0..200 basis points, default 0); the request records both fee and net payout amount. A cancelled request releases the full reserve through a second balanced ledger transaction.

This slice creates and cancels requests only. It does NOT sign or submit a multisig order, and it does not mark a withdrawal paid. Until the signer/order approval and TONAPI-confirmed payout executor are connected, no request can leave the queued_for_multisig state. Do not enable either feature flag in production until that complete execution path is available.
