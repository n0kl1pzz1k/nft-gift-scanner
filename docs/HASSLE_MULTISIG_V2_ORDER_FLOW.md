# HASSLE NFT Multisig V2 order lifecycle

This document records the transaction wire format from the pinned
`ton-blockchain/multisig-contract-v2` source revision
`9a4b13df6345c9c4068ca725e434b40f9ea5ca28`.

## Important configuration consequence

The HASSLE treasury is configured with a 2-of-3 threshold and an empty
proposer set. In Multisig V2, that means a new order must be created by one
of the three configured signers. The backend cannot autonomously originate
an order as a proposer, and a connected non-signer cannot create one.
Adding a backend proposer would change the multisig data and deterministic
address, so it is not a harmless runtime toggle.

## Transaction flow

1. A signer connects with TON Connect and proves ownership of the wallet.
2. The application reads the deployed treasury configuration and verifies
   its address, code hash, data hash, exact signer list, 2-of-3 threshold,
   and empty proposer list.
3. The application builds an unsigned `new_order` message and asks the
   signer wallet to send it to the multisig. The order body uses opcode
   `0xf718510f`, the auto-sequence sentinel (uint256 max), signer flag,
   signer index, expiration (uint48), and a reference to the action cell.
4. The resulting child Order contract is read from `get_order_address`.
5. A different configured signer reviews the complete decoded action list,
   recipient addresses, amounts, asset contract, payload hash, and expiry.
6. Only after independent review may that second signer send opcode
   `0xa762230f` with its signer index to the child Order contract.
7. The contract itself enforces the threshold and executes the order. The
   backend never holds a private key and never submits a signature.

## Current implementation boundary

`ton_multisig_transactions.py` creates unsigned transaction bodies only.
It accepts marketplace transfer payloads only when NFT/Jetton transfer
recipients can be decoded. Governance and unknown actions are rejected at
proposal preparation. Prepared orders are explicitly not marked safe to
approve. No signing or execution endpoint is exposed.

Live order creation and approval remain disabled until the exact multisig
contract is initialized on mainnet and the read-only treasury endpoint
verifies the live code/data hashes and signer set. An expected address or a
successful contract compilation is not proof of deployment.
