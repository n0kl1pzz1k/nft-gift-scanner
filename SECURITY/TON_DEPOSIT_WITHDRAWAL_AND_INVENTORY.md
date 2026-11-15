# TON wallet security and seller-owned NFT inventory

## Supported product boundary

GIFT RADAR supports Telegram Stars (XTR) as the only in-Mini-App purchase currency. TON is the only blockchain network and TON Connect is the only wallet protocol. No token/Jetton checkout or other blockchain payment route is supported.

## Implemented foundations

- TON Connect wallet linking and server-side TON Proof verification for TON mainnet/testnet.
- Wallet proof challenge lifecycle, StateInit/address/signature verification and a private colocated verifier service.
- Independent TonAPI and TON Center NFT state adapters.
- Seller NFT import records with canonical collection/item identity and immutable ownership evidence.
- Private seller listing drafts. Draft creation does not transfer the NFT, change custody state or publish a public listing.
- Reservation, deal, dispute and reconciliation data structures.
- Append-only double-entry financial ledger and Stars invoice/refund records.
- Idempotent TON withdrawal request/ledger-hold foundations with multi-approver and remote-signer release gates.

## Wallet and NFT verification rules

A wallet connection proves control of a wallet, not ownership of a specific NFT. The server validates TON Proof and the NFT verifier independently checks the exact collection, item and current owner through two configured providers. Unknown collections, provider disagreement, stale evidence or incomplete identity fail closed.

Only TON blockchain NFTs and approved collectible types belong in the TON inventory. A Telegram profile displaying a gift is not proof that the user controls a transferable TON NFT. The platform never requests Telegram login codes, 2FA passwords, seed phrases or private keys.

## Ledger and withdrawal safeguards

Withdrawal requests are idempotent and reserve the user's available ledger liability in a request-specific hold. Ambiguous broadcast outcomes must remain quarantined for reconciliation. The backend does not store user wallet credentials. TON signing and broadcasting require explicit user approval or a separately reviewed treasury signer process.

## Operational boundary

No live payment or NFT transaction is enabled by CI success alone. Production release requires the current code, secure secrets in the existing secret manager, data-preserving database cutover, legal/tax review, data-residency review, security review and operational sign-off.
