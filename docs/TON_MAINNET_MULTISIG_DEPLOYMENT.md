# HASSLE NFT TON Mainnet Multisig Deployment Plan

Status: PREPARED, NOT DEPLOYED. No multisig treasury address exists yet.

## Chosen contract

Use the TON Core `ton-blockchain/multisig-contract-v2` multi-owner wallet implementation, not the legacy Fift tutorial contract and not the TON Multisig web UI. The public `multisig.ton.org` interface currently identifies itself as TESTNET and must never be used to deploy this production treasury.

Upstream source: https://github.com/ton-blockchain/multisig-contract-v2\nPinned revision: `9a4b13df6345c9c4068ca725e434b40f9ea5ca28` (reviewed source revision; CI must build and test this exact commit)
Upstream documentation describes N-of-M approvals, immutable contract logic after deployment, and links Zellic and Trail of Bits audit reports:
- https://github.com/ton-blockchain/multisig-contract-v2/blob/master/audits/Multisig_Zellic_Audit_Report.pdf
- https://github.com/ton-blockchain/multisig-contract-v2/blob/master/audits/202403TON_Foundation_Multisignature_Wallet_Report_%2B_Fix_Review.pdf

The contract's order payload can execute arbitrary actions, including changing signers and creating or approving orders. Every signer must inspect the full action list and destination/amount before approving.

## HASSLE NFT signer configuration

Network: TON Mainnet (workchain 0)
Threshold: 2
Signer count: 3
Proposers: none configured separately; signers can propose orders.
Arbitrary sequence numbers: disabled.

Signer addresses (canonical user-provided addresses):
1. Primary: `UQA3K2PGXyotga99en2ZXE_Ne9wY5u-lC07oQK6bRdxtENfs`
2. Signer 2: `UQDqyqwz6TKotnrjHnEtWnlpZgEwNj5I54n5bAOmFivBR8ys`
3. Signer 3: `UQCO3ghYMh52rbhO5leQQxu4AAUKz8DuW2wSVeZRN_MiHJyI`

All three addresses were observed as active Wallet V5R1 accounts, and their on-chain `get_public_key` getters returned successfully. The backend verifier now pins this exact ordered signer list rather than accepting any arbitrary three addresses. The contract-v2 configuration uses signer wallet addresses; the public keys were useful for validating wallet control but are not the signer entries in this contract's config.

## Deployment safety gates

- Deploy only from a reviewed, pinned upstream source revision. Do not silently follow an unpinned upstream branch in production.
- Compile and run the upstream contract test suite before deployment.
- Derive and record the expected contract address from the exact compiled code and config before any wallet signs.
- Show the complete initialization transaction in TON Connect/Tonkeeper; the initiating signer must verify network=mainnet, derived contract address, code/config hash, and attached amount.
- Do not ask for, collect, transmit, or store seed phrases or private keys.
- After deployment, independently query the deployed contract's `get_multisig_data` and verify threshold=2, exact three signer addresses, no unexpected proposers, and expected code hash. The getter returns `(next_order_seqno, threshold, signers, proposers)`; it does not expose the `allowArbitrarySeqno` bit, so confirm the deterministic address and code/data StateInit hash as well.
- Make a small controlled treasury test transaction requiring two distinct signer approvals; verify one signature cannot execute it and two can.
- Only after all checks pass may the backend treasury address be configured and payment feature flags reconsidered.

## Current blockers

- No mainnet multisig contract has been deployed.\n- The pinned upstream build/test job is being added to CI; its green result is required before preparing a deployable StateInit artifact.
- No deployment transaction has been signed or broadcast.
- No on-chain multisig configuration has been verified.
- Real TON and Telegram Stars checkout remain disabled.
- Backend verification now calls the actual `get_multisig_data` getter through TONAPI, decodes the signer/proposer Hashmap BOCs, and requires exactly 2-of-3 with the three owner-approved signer addresses in the configured order and no proposers. The getter does not expose `allowArbitrarySeqno`; production verification must additionally match the precomputed deterministic contract address and compiled code hash derived from the reviewed StateInit.

## Funding note

The initiating signer must have enough TON for the deployment message, contract initial balance, and network fees. Do not assume that the recent 0.01 TON activation transfers are sufficient. Check the exact fee and deployment amount in the reviewed deployment transaction before signing.
