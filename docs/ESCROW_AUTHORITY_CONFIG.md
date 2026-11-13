# Escrow authority configuration

**Status:** approved design for the testnet MVP; implementation is a separate reviewed step.
**Network:** TON testnet only. No production keys, mainnet addresses or real funds.

## Goals

Signer and arbitrator membership is configuration, not source-code identity. The contract and deployment tooling must not contain named people, personal wallets, or operator-specific addresses. Every deal receives a frozen authority-set snapshot.

## MVP configuration

Each escrow deployment receives exactly three distinct signer addresses and three distinct arbitrator addresses:

```json
{
  "schema_version": 1,
  "network": "ton-testnet",
  "deal_id": "<256-bit deal identifier>",
  "signers": {
    "members": ["<signer-1>", "<signer-2>", "<signer-3>"],
    "threshold": 2
  },
  "arbitrators": {
    "members": ["<arbitrator-1>", "<arbitrator-2>", "<arbitrator-3>"],
    "threshold": 2
  }
}
```

The values above are placeholders, not usable addresses. The first end-to-end testnet flow uses the minimal 2-of-3 policy. Membership is supplied at deal creation/deployment and validated before deployment.

## Validation rules

- Exactly three members in each set for MVP; all addresses must be valid, non-zero and pairwise distinct within each set.
- Signer and arbitrator sets must be disjoint in MVP. Seller and buyer cannot be members of either authority set.
- Threshold must be exactly 2 for the first testnet vertical slice. The schema carries the threshold explicitly so a later reviewed version can support other quorum sizes without hardcoding people.
- The network identifier, deal ID, escrow address, action, amount/currency where applicable, Telegram charge reference hash, expiry and unique nonce are included in signed authorization data.
- A signature or vote is counted once per configured member. Replays, cross-deal reuse, wrong-network messages, expired authorizations and conflicting payloads are rejected.
- The configuration hash is stored with the deal and exposed by a getter for backend reconciliation.

## Lifecycle and rotation

- The authority set is immutable for an active deal. Changing a global operator configuration affects only future deals.
- No single operator, admin API call or one signer can rewrite the authority set for an existing escrow.
- A future authority rotation mechanism must be a separately reviewed protocol change. It must preserve the current deal's quorum, record old/new configuration hashes and prevent a compromised minority from replacing the set.
- Keys remain in independent external signers/HSMs. The application stores public addresses and non-secret configuration only. It never stores seed phrases or private keys.

## Operational separation

- Payment signers attest only facts supported by authenticated Telegram Stars events and reconciliation evidence. They do not decide disputes.
- Arbitrators decide only the dispute resolution proposal under the published policy. They do not attest payment.
- Backend authorization configuration is validated against the on-chain immutable snapshot before sending any privileged message.
- Missing or invalid configuration fails closed: no escrow deployment and no NFT release.

## Acceptance tests for this step

1. A valid 3+3 configuration with 2-of-3 thresholds is accepted.
2. Duplicate, zero, malformed, overlapping and incomplete member sets are rejected.
3. Changing environment-level membership does not mutate an already deployed deal snapshot.
4. One signer cannot meet a 2-of-3 payment quorum; two distinct configured signers can.
5. One arbitrator cannot meet a 2-of-3 dispute quorum; two distinct configured arbitrators can.
6. Duplicate votes, outsider votes, cross-deal replay and wrong-network authorizations are rejected.
7. Getters return the exact immutable configuration and configuration hash.

The existing prototype does not yet satisfy these acceptance tests. This document defines the target; it is not evidence of implementation or testnet deployment.
