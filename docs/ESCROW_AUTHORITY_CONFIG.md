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
  },
  "neutral_resolver": "<platform-approved-neutral-custodian-address>"
}
```

The values above are placeholders, not usable addresses. The first end-to-end testnet flow uses the minimal 2-of-3 policy. Membership is loaded only from the platform's access-controlled authority registry by the backend at deal creation/deployment. It is never accepted from seller/buyer request fields. The backend validates it before deployment.

## Sole source of truth and validation enforcement

- The platform authority registry is the sole source of signer and arbitrator addresses. It is not editable through seller or buyer APIs, Telegram Mini App payloads, deal metadata or client-supplied wallet addresses.
- Deal creation accepts commercial terms and participant wallet addresses only. The backend injects authority membership from the platform registry, computes a canonical configuration hash and stores an audit record.
- Backend rejects any request attempting to set or override signer/arbitrator addresses. It also rejects a configuration where seller/buyer appears in either set.
- Contract initialization verifies the platform authorization over the exact network, deal ID, escrow address and configuration hash, then stores an immutable per-deal snapshot. Contract messages from seller/buyer cannot write authority fields.
- Every privileged action is checked against the on-chain snapshot. Backend compares its cached configuration hash with the contract getter before sending the action.

## Validation rules

- Exactly three members in each set for MVP; all addresses must be valid, non-zero and pairwise distinct within each set.
- Signer and arbitrator sets must be disjoint in MVP. Seller and buyer cannot be members of either authority set.
- The neutral resolver/custodian address is selected only from the platform registry, is distinct from seller/buyer and authority members, and is fixed per deal. It is the bounded custody handoff destination if the final appeal deadline expires without a safe ownership decision.
- Threshold must be exactly 2 for the first testnet vertical slice. The schema carries the threshold explicitly so a later reviewed version can support other quorum sizes without hardcoding people.
- The network identifier, deal ID, escrow address, action, amount/currency where applicable, Telegram charge reference hash, expiry and unique nonce are included in signed authorization data.
- A signature or vote is counted once per configured member. Replays, cross-deal reuse, wrong-network messages, expired authorizations and conflicting payloads are rejected.
- The configuration hash is stored with the deal and exposed by a getter for backend reconciliation.

## Lifecycle and rotation

- The authority set is immutable for an active deal. Changing a global operator configuration affects only future deals.
- No single operator, admin API call or one signer can rewrite the authority set for an existing escrow.
- Normal membership is immutable for an active deal. Emergency rotation after one signer compromise is allowed only through a dedicated contract operation requiring 2-of-2 signatures from the two remaining original signers; it removes the compromised key, adds a new platform-approved key, preserves the 2-of-3 threshold and records old/new configuration hashes. Seller/buyer cannot request or approve the rotation. If both remaining signers are not available, privileged actions stay paused and the deal follows the bounded escalation policy. A compromised key alone cannot rotate or unpause the deal.
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
8. Seller/buyer API payloads cannot override authority members; attempted injection is rejected.
9. A single compromised signer cannot authorize payment, release, dispute resolution, recovery, pause bypass or key rotation; emergency rotation requires both remaining original signers.
10. Seller/buyer cannot set or replace the neutral resolver; missing resolver configuration blocks deal creation.

The existing prototype does not yet satisfy these acceptance tests. This document defines the target; it is not evidence of implementation or testnet deployment.
