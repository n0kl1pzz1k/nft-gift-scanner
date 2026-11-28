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
TONAPI events. It matches the exact treasury address, exact nano amount, unique
comment, configured network, non-scam status, and non-in-progress event. It then
uses one database transaction to:

1. insert a unique chain event;
2. post the balanced credit into the existing marketplace ledger
   (\`user_balance\`, internal currency \`TON\`, displayed as GRAM);
3. update the GRAM materialized balance;
4. mark the deposit intent confirmed.

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
