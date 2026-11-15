# GIFT RADAR TON Proof sidecar

Private, loopback-only TON Connect `ton_proof` verifier launched by the existing FastAPI Railway service. It does not require a separate Railway service, public domain, PostgreSQL connection, mnemonic, or private key.

## Runtime

The repository root Nixpacks build installs Node.js and compiles this TypeScript package. `scripts/start.sh` starts the verifier on `127.0.0.1:8081` and the FastAPI API on Railway's public application port.

Required process variables:
- `TON_PROOF_INTERNAL_TOKEN`: generated at process start when the local sidecar is used; can be supplied explicitly.
- `TON_PROOF_ALLOWED_DOMAINS`: exact comma-separated dApp hostnames. The startup script includes the current Cloudflare production hostname and legacy Pages preview hostnames by default.
- `TON_PROOF_ALLOWED_DOMAIN_SUFFIXES`: comma-separated project-scoped preview suffixes. The startup script defaults to `-nft-gift-scanner.fj7kmzkhn9.workers.dev`, which permits only preview hosts for this Worker project, not arbitrary `workers.dev` domains.
- `TON_PROOF_PORT`: optional private port, default `8081`.
- `TON_MAINNET_ENDPOINT` and `TON_TESTNET_ENDPOINT`: optional TON v4 RPC endpoints used only for the reviewed WalletTg public-key getter fallback.

The FastAPI backend calls the sidecar at `http://127.0.0.1:8081`. Requests require the internal header and the sidecar binds only to loopback.

## Routes

- `GET /health`: private health probe.
- `POST /v1/challenges`: internal only; creates a cryptographically random, 10-minute challenge bound to the authenticated Telegram user ID.
- `POST /v1/verify`: internal only; verifies the TON Connect domain, timestamp, StateInit-derived wallet address, supported wallet public key and Ed25519 signature. A challenge is consumed exactly once.

Challenge records are held in the private sidecar process. A restart invalidates outstanding challenges, which fail closed. Keep this Railway service at one replica while using the in-process challenge store; horizontal scaling requires a shared atomic challenge store before replicas are increased.

Only TON mainnet (`-239`) and testnet (`-3`) network IDs are accepted. Unsupported wallet contracts fail closed. The verified wallet binding is persisted by the FastAPI backend in its existing marketplace database.

## Security

- The sidecar listens on loopback only and is not exposed as a public service.
- No seed phrase, private key, wallet password, or signing request is accepted.
- TON Proof proves control of the wallet address, not ownership of a particular NFT. NFT ownership is checked separately by TonAPI and Toncenter.
- A proof challenge is bound to the Telegram user and cannot be replayed after successful consumption.
- Do not increase Railway replicas until the challenge store is moved to a shared atomic backend.
