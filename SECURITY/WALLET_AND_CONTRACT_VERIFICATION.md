# Wallet Compatibility and Anti-Counterfeit Asset Verification

Status: implementation specification; no wallet signing, deposits, transfers or live payment processing enabled.
Branch: security/isolated-services
Date: 2026-11-13

## 1. Telegram Mini App compatibility boundary

Telegram's official Blockchain Guidelines require Mini App blockchain functionality to use TON blockchain assets and TON Connect SDK wallet interactions. Wallets may be multichain, but the Mini App must not add independent TRON/EVM wallet connection protocols or use non-TON chain interactions except narrowly allowed bridging scenarios.

Therefore:
- In the Telegram Mini App, wallet discovery/connection is through the unmodified official TON Connect SDK and its wallet registry.
- Compatible TON wallets include Wallet, Tonkeeper, MyTonWallet and other wallets exposed by the official TON Connect registry. Do not hardcode a closed wallet list or spoof wallet identities.
- A multichain wallet may connect through TON Connect, but GIFT RADAR Mini App interactions are restricted to permitted TON blockchain functionality.
- USDT-TRC20 is not an in-Mini-App payment rail. Do not expose TRON signing or TRC-20 checkout inside the Mini App.
- USDT on TON is a TON jetton and may be considered only if the exact marketplace use case is permitted under Telegram's payment rules.
- Stars (XTR) remain the required payment method for digital goods/services sold inside Telegram bots and Mini Apps. NFT marketplace transaction classification must be reviewed before payment implementation.
- No seed phrases, private keys, recovery codes or wallet passwords are ever requested or stored.

Official references:
- Telegram Blockchain Guidelines: https://core.telegram.org/bots/blockchain-guidelines
- TON Connect overview: https://docs.ton.org/applications/ton-connect/overview
- Telegram Stars: https://core.telegram.org/bots/payments-stars

## 2. Threat model: counterfeit / "flash USDT"

A ticker, symbol, token name, logo, wallet UI balance, QR code, screenshot, explorer label, webhook payload or user-supplied transaction hash is not proof that a payment is real.

The phrase "flash USDT" is commonly used for counterfeit or fabricated token displays and fake payment claims. A fake token can copy the name, symbol, decimals and logo of a legitimate asset. A fabricated or unconfirmed transaction can also be presented as payment evidence.

The only acceptable asset identity is a chain-specific, pre-approved contract/master address on the expected network, independently checked against authoritative issuer documentation and on-chain state.

## 3. Canonical asset registry

Every accepted asset is a version-controlled, reviewed configuration record with:
- chain_id / network (mainnet vs testnet explicitly separated)
- canonical master/contract address in normalized form
- issuer/source URL and date checked
- symbol and decimals as display metadata only
- contract code hash / implementation identity where meaningful
- proxy/upgradeability/admin/blacklist/pause/mint controls and risk notes
- approved use cases and Telegram policy classification
- effective_from, approved_by, second_reviewer, status and change history

No token can be added by a user, seller, administrator UI or remote metadata response. Adding/changing an asset requires a reviewed code/config change and two-person approval.

### Official USDT on TON

Tether lists the TON Jetton USDT master address as:

EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs

This is a candidate canonical address for mainnet verification, not permission to enable payments. The runtime must independently verify the chain/network and master contract state before allowlisting. Do not infer GRAM's canonical contract from its ticker or third-party listings.

### Official USDT on TRON

Tether lists the TRC-20 USDT contract as:

TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t

This is documented for a future legally permitted external integration only. It must not be used as an in-Mini-App wallet or payment rail. A TRON adapter, if later approved for a distinct external product, must be isolated and disabled by default.

Official issuer reference: https://tether.to/supported-protocols/

## 4. TON Jetton verification pipeline (read-only)

Use at least one trusted TON node/provider and an independent source for reconciliation. A single indexer response is not sufficient.

For every incoming payment candidate:

1. Require mainnet network identity and normalize raw TON addresses before comparison.
2. Require the jetton master address to exactly match the active canonical asset registry entry.
3. Query the master contract's get-methods and validate expected jetton metadata/state, including decimals and mint/admin controls. Metadata never substitutes for the master address.
4. Derive the recipient's jetton wallet address by calling the approved master contract's get_wallet_address(recipient) method.
5. Verify the transaction's sender is exactly that derived jetton wallet. Never trust a user-supplied jetton wallet address.
6. Parse the actual on-chain message/trace. Require the correct transfer_notification opcode (0x7362d09c), decode raw integer amount and payload according to the TL-B schema, and validate recipient/invoice binding.
7. Verify the receiving transaction and execution phases. Follow the full message trace; reject bounced, failed, incomplete or ambiguous traces.
8. Require a unique, unused payment intent and unique chain event identity. Deduplicate transaction/message/event references in the database.
9. Require configured finality/confirmation policy and reconcile against an independent node/source before posting a payment event.
10. Persist immutable evidence: network, master, derived jetton wallet, sender, recipient, raw amount, decimals, tx hash, transaction LT/time, payload/invoice, provider observations and verification version.

TON-specific mandatory protections:
- Never accept a jetton based on name/symbol/image.
- Never accept a jetton wallet without verifying its master-to-wallet relationship.
- Reject unexpected master contracts even if they imitate USDT/GRAM metadata.
- Reject duplicate invoice IDs, replayed notifications, malformed payloads, bounced flows and amount mismatches.
- If the trace is unavailable or conflicting between providers, mark the payment REVIEW_REQUIRED; do not credit or fulfil.
- Treat any change in master code/admin/upgradeability state as a re-approval event.

Official technical guide: https://docs.ton.org/applications/payments/jettons

## 5. TRON TRC-20 verification pipeline (not for Mini App use)

Only if separately approved for a lawful external product:
- Require exact mainnet chain and canonical TRC-20 contract from issuer registry.
- Query the solidified transaction body and solidified execution receipt. A FullNode transaction body or unconfirmed event is not final settlement evidence.
- Require receipt.result == SUCCESS.
- Decode the Transfer event emitted by the exact allowlisted token contract. Validate from, to, raw integer value and event identity.
- Verify the recipient and amount against the server-created payment intent.
- Confirm solidified inclusion, deduplicate by txID + log/event index + contract address and reconcile using a second trusted data source.
- Reject any event emitted by a lookalike token contract, even if the event name is Transfer and symbol says USDT.
- If receipt is absent, keep PENDING; do not treat it as failed or successful from one empty query.

Official references:
- Tether supported protocols: https://tether.to/supported-protocols/
- TRON confirmation semantics: https://developers.tron.network/docs/confirmation-semantics
- TRON solidified transaction receipt: https://developers.tron.network/reference/gettransactioninfobyid-1
- TRON TRC-20 interaction and Transfer event: https://developers.tron.network/docs/trc20-contract-interaction

## 6. Payment state machine

Allowed states:
CREATED -> AWAITING_PAYMENT -> OBSERVED_UNCONFIRMED -> VERIFYING -> CONFIRMED -> SETTLED

Exception states:
EXPIRED, UNDERPAID, OVERPAID, WRONG_ASSET, WRONG_NETWORK, UNKNOWN_CONTRACT, INVALID_TRACE, BOUNCED, FAILED_EXECUTION, DUPLICATE, REVIEW_REQUIRED, REFUNDED.

Only the payment verification service may emit CONFIRMED after all checks pass. Client, seller, support staff, owner and admin roles cannot set CONFIRMED or directly mutate the ledger.

The order/ledger posting transaction must be atomic and idempotent. Store raw amounts as integers in base units; never use floating-point arithmetic. Use a unique database constraint on the chain event identity and payment intent. The same event can never settle two orders.

## 7. Wallet UX and user protection

- Show the wallet name/address, network and connected state clearly.
- Before any user-authorized transaction request, show asset, exact amount, recipient, network, purpose and estimated network fee.
- No automatic signature requests, hidden approvals, unlimited allowances or surprise transactions.
- For simple deposit verification, prefer a receive-only flow where the user sends from their wallet and the backend independently observes it.
- If a transaction signature is needed for an allowed TON action, use TON Connect and require an explicit user gesture and wallet confirmation.
- Clearly warn that fake tokens can display the same ticker and logo as real assets.
- Display a short verified contract identifier and a details view with the full master address.
- A pending transfer must never appear as spendable/available funds.

## 8. Fail-closed rules and monitoring

- RPC/indexer timeout, disagreement, stale head, malformed trace, missing receipt, unknown master, changed contract code or unsupported network => fail closed.
- Never fall back to trusting a browser, screenshot, webhook-only signal or single provider.
- Alert on unknown token contracts, repeated fake deposits, event replay, address mismatch, large amount mismatch, reorg/trace anomalies and provider divergence.
- Keep verification evidence and configuration versions in append-only audit records.
- No automated refunds/transfers/withdrawals are part of this read-only verifier.

## 9. Ten specialist workstreams (parallel review plan)

These are ten independently reviewable work packages. They are not represented as ten live AI agents unless an actual agent runtime is provisioned.

1. Telegram policy reviewer — Mini App wallet and payment compliance.
2. TON Connect engineer — official SDK, manifest, wallet registry, session lifecycle.
3. TON Jetton engineer — master/wallet derivation and message/trace parser.
4. Token authenticity reviewer — issuer registry, contract code/admin risk and GRAM verification.
5. Payment backend engineer — payment intent/state machine and idempotency.
6. Ledger engineer — double-entry postings, immutable journal and reconciliation.
7. Security architect — threat model, service isolation and least privilege.
8. QA engineer — counterfeit token, replay, duplicate, malformed payload and race-condition tests.
9. DevOps/SRE engineer — RPC redundancy, monitoring, rate limits, backup and incident response.
10. UX/accessibility engineer — wallet selection, clear transaction confirmation and anti-scam messaging.

## 10. Release blockers

No production payments until:
- Exact marketplace transaction model is cleared for Telegram Mini App rules.
- Stars checkout is tested where required.
- TON Connect integration passes wallet compatibility and origin/manifest checks.
- Canonical asset registry has independently reviewed addresses.
- TON fake-jetton tests prove lookalike master/wallet rejection.
- Payment state machine, idempotency and immutable ledger tests pass.
- Provider outage and disagreement tests fail closed.
- Security review, load test and backup-restore drill pass.
- GRAM remains disabled until its exact canonical master, decimals, issuer provenance and risk profile are independently approved.
