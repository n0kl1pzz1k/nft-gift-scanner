# GIFT RADAR TON Proof service

Private-network service for verifying TON Connect `ton_proof` and binding a public wallet address to an already authenticated Telegram user.

## Required runtime variables
- `DATABASE_URL`: Railway PostgreSQL private connection URL.
- `TON_PROOF_INTERNAL_TOKEN`: high-entropy service-to-service secret. Never commit it or expose it to the browser.
- `TON_PROOF_ALLOWED_DOMAINS`: comma-separated exact dApp domains (no scheme or path).
- `PGSSLROOTCERT_PEM`: optional PEM-encoded CA certificate when the PostgreSQL server uses a private CA. Certificate verification is always enabled; the service does not offer an insecure TLS bypass.
- `TON_MAINNET_ENDPOINT` and `TON_TESTNET_ENDPOINT`: optional TON v4 RPC endpoints used only for reviewed WalletTg getter fallback.
- `PORT`: supplied by Railway.

## Routes
- `GET /health`: health probe.
- `POST /v1/challenges`: internal only; creates a 10-minute, single-use payload bound to Telegram user ID.
- `POST /v1/verify`: internal only; verifies domain, timestamp, StateInit-derived address, supported wallet public key and Ed25519 signature; consumes challenge atomically and binds the public address.

Only TON mainnet/testnet network IDs are accepted. Unsupported wallet contracts fail closed. This service never receives mnemonic phrases, private keys, or signing requests and cannot submit transactions.

## Deployment
Deploy as a separate Railway service from this repository using this directory as the Docker build context. Keep it private; do not generate a public domain. Configure the internal token and allowed domains in Railway Variables. The FastAPI backend should be the only caller.

PostgreSQL TLS certificate verification is mandatory. The service trusts the runtime's system CA store by default; if the Railway database certificate is issued by a private CA, provide its PEM certificate through `PGSSLROOTCERT_PEM`. Do not set `PGSSLMODE=disable` or disable certificate verification to make a connection succeed.
