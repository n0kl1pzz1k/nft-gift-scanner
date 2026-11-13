# Kazakhstan launch legal and data-residency gate

**Assessment date:** 13 November 2026  
**Status:** engineering compliance gate, not a legal opinion.  
**Release state:** NOT APPROVED. All live payment, custody, crypto deposit, escrow and withdrawal flags remain disabled.

## 1. Regulatory perimeter effective 1 May 2026

The National Bank of Kazakhstan states that amendments to the Law on Digital Assets introduced a regulated market structure effective 1 May 2026. The National Bank identifies:
- operators of digital asset trading platforms, including platforms that organize trading in digital assets;
- operators exchanging unsecured digital assets, including exchange/purchase/sale services for clients;
- digital financial asset platform operators;
- licensing or registration with the National Bank, depending on the activity;
- infrastructure, capital, risk management, compliance and AML/CFT requirements for regulated providers.

The amended Article 12-1 of the Digital Assets Law states that circulation/organization of circulation of unsecured digital assets in Kazakhstan is permitted only through the specified licensed or registered providers, subject to the law's exceptions. The precise classification of GIFT RADAR's activities must be determined against the actual product and legal entity, not the labels used in the UI.

**Primary sources**
- National Bank framework: https://nationalbank.kz/en/page/digital-assets-legal-framework
- National Bank notice, 30 April 2026: https://nationalbank.kz/en/news/informacionnye-soobshcheniya/19193
- Law No. 193-VII, as amended 1 May 2026: https://adilet.zan.kz/eng/docs/Z2300000193/info
- Amended Article 12-1 text: https://old.adilet.zan.kz/rus/docs/Z2300000193

## 2. GIFT RADAR risk classification questions

The current product design includes user listings, price display, reservations, order matching, escrowed NFTs, payment acceptance, internal balances, settlement, and contemplated withdrawals. Those functions can create a materially different regulatory profile from a passive classified-advertising site.

Kazakhstan counsel must give a written answer for each of these questions before activation:

1. Are Telegram collectible gifts, TON NFTs, GRAM and USDT each digital assets under the applicable statutory definitions, or are any of them treated differently?
2. Does operating a marketplace that matches buyers and sellers, reserves assets, or coordinates transfers constitute operating a digital asset trading platform or otherwise organizing circulation?
3. Does controlling an escrow address, holding NFT assets, or crediting a transferable/withdrawable internal balance constitute custody or another regulated service?
4. Does accepting GRAM or USDT, holding client liabilities, or settling trades create exchange, payment-service, remittance, or other regulated activity?
5. Does conversion between USDT and GRAM constitute exchange activity even when presented as a display quote?
6. Which National Bank licence, accounting registration, AIFC authorization, sandbox approval, or other permission is required for the chosen legal entity and user geography?
7. What AML/CFT, sanctions, CDD, source-of-funds, transaction-monitoring, suspicious-transaction reporting, recordkeeping and travel-rule obligations apply?
8. How must customer assets and seller liabilities be segregated, safeguarded, reconciled and treated on insolvency?
9. What consumer-protection, e-commerce, seller-of-record, tax, invoicing/fiscalization, VAT and cross-border requirements apply?

Until counsel confirms the precise model and any required permission is obtained, do not accept crypto, custody assets, enable escrow, credit withdrawable crypto balances, or broadcast withdrawals.

## 3. Personal-data localization

Article 12(2) of the Kazakhstan Law on Personal Data and Their Protection states that personal data are stored by the owner/operator or third party in a database located in the territory of Kazakhstan. The law's current text and any applicable cross-border transfer provisions must be reviewed by counsel against each processing flow.

Primary source:
- https://adilet.zan.kz/eng/docs/Z1300000094

The existing infrastructure audit identified Railway's live backend and PostgreSQL in IAD (US East / Ashburn), and the production backend had not been demonstrated to use the provisioned PostgreSQL database. That architecture is not evidence of Kazakhstan-localized personal-data storage.

Before production:
- Map every personal-data field: Telegram identifiers/profile data, order history, wallet addresses when linked to a person, support records, payment references, device/security logs and admin audit records.
- Identify the physical location of the primary database, replicas, backups, snapshots, object storage, logs, analytics, error reporting, support tooling and disaster-recovery copies.
- Ensure all databases/digital objects that contain personal data and are subject to localization are located in Kazakhstan.
- Review each processor/subprocessor, support access path, remote administrator access and cross-border transfer under the current law.
- Use data minimization, retention limits, deletion schedules, role-based access, encryption, breach response and processor agreements.
- Obtain written evidence from each cloud provider of the actual region and backup location. A deployment variable saying KZ is not evidence.

## 4. Product-surface constraints

Telegram's Bot Developer Terms and Stars documentation require Stars/XTR for digital goods and services sold inside Telegram bots and Mini Apps. This remains separate from the Kazakhstan regulatory analysis.

- Telegram digital-goods checkout: XTR/Stars only.
- Do not put a crypto checkout button, wallet transfer request, QR, external payment link or redirect into an in-Telegram purchase journey.
- TON Connect proves control of a public wallet address; it does not override the Stars-only rule.
- A standalone web marketplace with crypto is a separate product-surface question, not an automatic workaround. Counsel must review the actual discovery, marketing, deep-link, redirect and transaction journey.

Sources:
- https://telegram.org/tos/bot-developers
- https://core.telegram.org/bots/payments-stars

## 5. Mandatory technical release gates

The application must remain fail-closed until each item is independently evidenced:
- GIFT_RADAR_STORAGE_BACKEND=postgres and production database connectivity is proven.
- Primary database, replicas, backups and relevant data stores have verified Kazakhstan residency.
- Kazakhstan legal sign-off confirms the exact business model and required licences/registrations.
- Tax/accounting sign-off covers Stars, crypto, commissions, seller liabilities and receipts.
- Independent smart-contract and backend security review is complete.
- Asset registry has the canonical GRAM and USDT TON contract addresses, decimals and authenticity evidence.
- Dual-provider deposit observation, finality, idempotency, duplicate detection and manual reconciliation pass.
- Smart-contract source compiles, unit tests and adversarial tests pass, deployment code hash is independently reviewed, and an external audit is completed.
- Withdrawal signer uses an HSM/KMS, two-person approval, strict destination/asset policy, broadcast reconciliation and no automatic retry on ambiguous results.
- End-to-end purchase, delivery, refund, dispute, replay and concurrency tests pass.
- Incident, key rotation, backup restore, reconciliation and customer complaint runbooks are approved.

The following environment gates must be set only after written evidence is attached:
- KZ_LEGAL_REVIEW_APPROVED=true
- KZ_DATA_RESIDENCY_VERIFIED=true
- KZ_TAX_REVIEW_APPROVED=true
- KZ_SECURITY_REVIEW_APPROVED=true

These variables are guardrails, not proof of compliance. The release manager must retain the underlying written opinions, provider attestations, audit report and test evidence. No engineer may set them merely to bypass a disabled feature.

## 6. Decision

**Current decision: NO-GO for live crypto, escrow, custody, seller payouts and withdrawals.**  
Stars remains disabled until its existing Telegram terms, backend, storage, support and reconciliation gates are approved. This document records the release criteria; it is not legal advice and does not authorize deployment or the movement of funds/assets.
