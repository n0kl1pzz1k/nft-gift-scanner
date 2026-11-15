# GIFT RADAR: Stars checkout and TON NFT custody

## Product rule

- Buyers pay for digital goods inside the Telegram Mini App exclusively with Telegram Stars (XTR).
- TON Connect is the only wallet protocol.
- TON is the only blockchain network used for NFT ownership, custody and transfer operations.
- No token/Jetton checkout, stablecoin payment route or non-TON network is part of the marketplace.

A Stars payment is an off-chain Telegram payment. It is not held by a TON smart contract and must never be described as such.

## Implemented payment controls

The Stars foundation uses Telegram invoice payloads, terms acceptance, pre-checkout validation, authenticated `successful_payment` updates, unique Telegram charge IDs, idempotent settlement records, refund requests, admin-claimed refund execution and reconciliation for ambiguous outcomes.

A pre-checkout approval is not proof of payment. The backend treats an order as paid only after the authenticated Telegram successful-payment update and exact invoice/buyer/currency/amount matching.

## TON wallet and NFT controls

TON Connect is used for user-approved wallet interaction. Server-side TON Proof verifies wallet control. Independent TON indexers are used to verify NFT identity, current ownership and transfer evidence. A wallet proof by itself does not prove that the wallet owns a particular NFT.

The escrow contract prototype models deal participants, NFT custody, payment confirmation by the platform, delivery, dispute and recovery states. The Stars payment is coordinated by the backend and ledger, not transferred into the TON contract.

## Settlement and refund risk

Telegram Stars can be refunded or withheld. The platform must retain the Telegram charge identifier, support `/paysupport`, maintain a refund/reconciliation process and avoid claiming that a ledger entry is equivalent to a completed seller payout. NFT delivery and Stars refund risk must be handled explicitly in the product terms and release policy.

## Release controls

Stars and real-deal feature flags remain fail-closed until the required legal, tax, data-residency, security, database and operational reviews are approved. No mainnet payment or wallet transaction should be broadcast automatically by the backend. Users approve wallet transactions in TON Connect.
