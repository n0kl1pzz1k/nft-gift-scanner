# GIFT RADAR Legal and Compliance Review Packet

**Status:** preparation checklist for qualified counsel; not a legal opinion, licence, AML approval or launch authorization.  
**Product:** peer-to-peer marketplace for tokenized Telegram gifts / NFTs, operated through a Telegram Mini App and associated backend.  
**Current technical mode:** testnet and test data only.

## Executive issue requiring early decision

Telegram classifies digital goods and services sold through bots and Mini Apps as Stars-only. The Bot Platform Developer Terms also state that Stars shown in a bot's balance are not the developer's property and that Telegram is not a custodian. Stars can be used for specific developer purposes and rewards; the terms do not establish a general mechanism for splitting Stars to arbitrary marketplace sellers.

Telegram further discourages real-time sales of digital products and virtual items with inherent value that cannot be reclaimed, frozen, rescinded, invalidated or suspended after a successful dispute or chargeback.

**Counsel must answer before a real-money design is approved:**
1. Can the proposed peer-to-peer NFT resale model lawfully and contractually operate through Telegram Mini Apps under the current Bot Platform Developer Terms?
2. What seller payout method is permitted, operationally available and consistent with Telegram's Stars rules?
3. Who is the merchant of record, who owes delivery/refunds, and who legally holds any seller payable?
4. Does the operator become a custodian, payment intermediary, virtual-asset service provider, escrow provider or another regulated entity in the chosen jurisdiction?

Official references:
- https://telegram.org/tos/bot-developers
- https://core.telegram.org/bots/payments-stars
- https://telegram.org/tos/stars

## Jurisdiction and corporate structure

Counsel to confirm:
- Place of incorporation, registered office, actual management and operating locations.
- Beneficial owners, directors, control persons and required corporate disclosures.
- Countries where buyers and sellers may reside and where marketing will be directed.
- Whether the marketplace is principal, agent, broker, escrow agent, custodian or technical intermediary.
- Whether local licensing/registration applies to payment services, custody, virtual assets, exchange, brokerage or escrow.
- Whether a separate operating company, IP company or regulated partner is required.

## Digital assets and property rights

- Legal classification of Telegram collectible gifts, TON NFTs and associated metadata.
- What exactly the buyer acquires: token ownership, contractual rights, licence to metadata/artwork, or a combination.
- Seller's authority to list and transfer the NFT; collection/item authenticity and provenance.
- Treatment of wrapped, bridged, frozen, blacklisted, counterfeit or disputed assets.
- Intellectual-property licence and takedown process.
- Consumer disclosures for metadata availability, chain finality, wallet loss, smart-contract bugs and third-party protocol failures.
- Whether assets are legally reclaimable or reversible following fraud, chargeback or court order.

## Payments, custody and escrow

- Telegram Stars merchant model, refundStarPayment obligations, chargebacks and seller-settlement mechanism.
- Whether Stars may be accepted for secondary-market NFT resale and whether Telegram requires additional approvals.
- Legal and accounting treatment of Stars receipts, refunds, platform fees and seller payables.
- Whether TON/Jetton payment is permitted for any separate non-Telegram surface and how it must be segregated from the Mini App journey.
- Custody analysis for seller-deposited NFTs held by per-deal escrow contracts.
- Who controls contract deployment, payment attestations, dispute arbitration and emergency pause powers.
- Whether the on-chain arbitrator design constitutes custody or discretionary control.
- Required customer-asset segregation, reconciliation, safeguarding and insolvency treatment.

## AML, sanctions and fraud controls

- Applicability of AML/CFT, VASP, payment-services and sanctions laws.
- KYC/KYB thresholds for buyers, sellers, beneficial owners and high-risk activity.
- Sanctions screening and wallet screening expectations.
- Source-of-funds/source-of-wealth triggers and suspicious activity escalation.
- Transaction monitoring rules for rapid resales, layering, self-trading, wash trading, stolen assets and sanctioned addresses.
- Recordkeeping period, regulatory reporting and law-enforcement request process.
- Geographic restrictions and prohibited-person policy.

## Consumer, tax and data protection

- Seller and buyer terms, risk disclosures, fee disclosures and dispute rules.
- Refund, cancellation, failed delivery, mistaken transfer and account suspension procedures.
- Tax treatment of platform fees, seller proceeds, digital asset transfers, VAT/GST and withholding.
- Seller reporting and tax documentation requirements.
- Personal-data categories, purpose, lawful basis, retention, deletion and access controls.
- Data residency, cross-border transfers, subprocessors, breach notification and user rights.
- Whether identity verification data may be outsourced and under what contractual safeguards.

## Required legal deliverables

1. Written jurisdiction and entity-structure memorandum.
2. Written Telegram policy assessment, including Stars and secondary-market NFT resale.
3. Written seller payout and custody opinion.
4. AML/CFT and sanctions applicability assessment plus required control programme.
5. Tax memorandum covering platform and seller flows.
6. Reviewed buyer terms, seller terms, privacy notice, refund policy and dispute policy.
7. Written go/no-go decision for each payment rail and each supported country.

## Technical evidence to provide counsel and auditors

- Architecture: `docs/ESCROW.md`.
- Public API and contract messages: `docs/INTERFACES.md`.
- Ledger and settlement implementation, migrations and reconciliation queue.
- Stars invoice, pre-checkout, successful-payment and refund handlers.
- TON Connect and ton_proof verification design.
- Testnet contract source, compiler version, code hash, test reports and deployment transaction IDs (when testnet deployment is explicitly approved).
- Key-management/HSM architecture, signer permissions and incident runbook.
- Data-flow diagram, retention schedule, subprocessors and infrastructure regions.
- Threat model, penetration test, independent smart-contract and financial-logic audit reports.

## Launch blockers

- No written legal opinion for the operating jurisdiction.
- No written Telegram compatibility decision for valuable secondary-market NFTs and the seller payout model.
- No AML/KYC/sanctions determination and operating procedure.
- No external audit of the smart contract and financial settlement logic.
- No verified backup restore drill, alerting/on-call ownership or incident-response test.
- No production deployment approval.

Until every applicable blocker is closed, all payment and escrow integration remains testnet-only and production payment flags must remain disabled.
