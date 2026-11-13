# Kazakhstan Legal, Telegram Stars and Security Gate

**Review date:** 2026-11-13  
**Status:** preliminary product/compliance assessment; not a legal opinion. No real payments or live deals are approved by this document.

## 1. Binding product payment rule

GIFT RADAR sells/mediates digital goods inside a Telegram bot and Mini App. The only checkout rail permitted for those in-Telegram digital goods is Telegram Stars with currency `XTR`.

- Never offer TON, USDT, another cryptocurrency, a bank card processor, an external checkout link, or a webview as an alternative payment method for digital goods purchased inside Telegram.
- Do not redirect a Telegram buyer to an external payment flow to evade the Stars rule.
- Keep `TELEGRAM_STARS_PAYMENTS_ENABLED=false` and `MARKETPLACE_REAL_DEALS_ENABLED=false` in production until every activation gate is evidenced and separately approved.
- Crypto payment flags remain false. Asset allowlisting is not payment authorization.
- Wallet connection/proof may only prove control of a public wallet address. It does not prove ownership or transferability of a gift and must never request seed phrases, private keys, wallet passwords, or signatures that move assets.

Telegram's official Stars documentation says digital goods and services sold inside bots and Mini Apps must use Stars/XTR, requires checking `successful_payment` before delivery, retaining `telegram_payment_charge_id`, providing terms and payment support, and supporting refunds/disputes. See:
- https://core.telegram.org/bots/payments-stars
- https://telegram.org/tos/stars

## 2. Kazakhstan digital-asset regulatory perimeter

The National Bank's current digital-assets framework page describes the 2026 amendments and regulated digital-asset service providers. It identifies, among others, operators of digital-asset trading platforms and unsecured digital-asset exchange operators, and describes licensing/registration and AML/CFT obligations. AFSA separately warns that organizing digital-asset circulation in Kazakhstan without the applicable licence/authorization is prohibited.

Official references:
- Law of the Republic of Kazakhstan "On Digital Assets in the Republic of Kazakhstan", No. 193-VII, 6 February 2023, as amended: https://adilet.zan.kz/eng/docs/Z2300000193
- National Bank, Regulation of the digital assets market: https://nationalbank.kz/en/page/digital-assets-legal-framework
- National Bank announcement on the regulatory framework effective 1 May 2026: https://nationalbank.kz/en/news/informacionnye-soobshcheniya/19193
- AFSA warning on unlicensed digital-asset platforms: https://afsa.aifc.kz/afsa-warning-on-unlicensed-digital-asset-platforms/

**Unresolved legal classification:** counsel must assess whether the exact GIFT RADAR NFT/gift listing, matching, order, payment and transfer workflow constitutes "organising circulation" or operation of a regulated digital-asset trading platform, exchange, custody or another regulated service. The answer may depend on the asset's legal classification, whether it is a Telegram collectible gift or an on-chain NFT, whether the platform matches/executes trades, whether it handles assets or proceeds, where the operator is established, and where customers are located. Do not assume that a non-custodial design or Stars checkout alone removes licensing obligations.

Before launch, obtain a written Kazakhstan legal opinion addressing:
1. Legal classification of Telegram collectible gifts versus TON NFTs and other tokens.
2. Whether listing, order matching, price display, transaction facilitation, escrow, custody or transfer coordination requires a licence, registration, AIFC authorization, or sandbox participation.
3. Whether the operator is principal, agent, information intermediary, or marketplace operator; seller-of-record and consumer-facing responsibility.
4. AML/CFT, sanctions, suspicious-transaction, recordkeeping and customer-identification duties that apply to the chosen model.
5. Cross-border activity, governing law, consumer location, dispute forum and applicable foreign rules.
6. Whether Stars proceeds, platform fees, seller balances and any later seller payout create payment-service, financial, tax or other regulatory obligations.

No crypto checkout, exchange, swap, custody, wallet collection, escrow or seller crypto payout is to be implemented pending this opinion and any required regulator authorization.

## 3. Consumer, contract and tax controls

Kazakhstan consumer-protection guidance describes a written claim process and a 10-calendar-day response period where the seller rejects a consumer's demands. Electronic-commerce rules require material product, price and purchase-condition information to be supplied before contract formation. The product must not promise that Telegram's own support will resolve a marketplace dispute.

Official references:
- Consumer claim and response process: https://www.gov.kz/situations/464/1116?lang=en
- Rules on internal trade / e-commerce information duties: https://www.gov.kz/org/selhoz-satpaev/documents/details/226176?lang=ru
- Tax Code (current official tax rules should be checked with a Kazakhstan tax adviser): https://adilet.zan.kz/eng/docs/K2500000214
- State Revenue Committee, new Tax Code Q&A: https://www.gov.kz/org/kgd/press/article/details/232086?lang=en

Before accepting real orders, publish accessible Kazakh and Russian terms (as legally required), privacy notice, seller rules and payment support route. They must clearly identify:
- Operator legal name, registration/tax details and contact address.
- Who is the seller and who receives the Stars; whether GIFT RADAR acts as agent or principal.
- Exact gift/item identity, condition/status, price in Stars, platform fee, what is included and delivery deadline.
- Transfer/delivery method, evidence required, failure handling and dispute path.
- Refund eligibility, process, response times and the fact that Stars refunds are executed by the bot through Telegram's official method.
- Prohibited items, fraud reports, seller suspension and appeal rules.
- Applicable law and dispute resolution without waiving mandatory consumer rights.

Tax adviser must determine the correct operator/seller tax treatment, income recognition, accounting of Stars, platform commission, seller liabilities, receipts/fiscalization, VAT and cross-border reporting. Do not infer tax treatment from the fact that Stars are virtual units or that Telegram processes the user's purchase of Stars.

## 4. Personal-data localization and privacy

Kazakhstan's personal-data rules require data storage in a database and/or digital object located in Kazakhstan. The 2026 amendments and implementation rules must be checked against the exact data flows and effective dates. The platform stores or may store Telegram user IDs, account/profile data, order history, wallet public addresses, support requests and payment references.

References:
- Law on Personal Data and their Protection: https://adilet.zan.kz/eng/docs/Z1300000094
- Government guidance on data-subject rights: https://www.gov.kz/situations/421/1787?lang=en

**Launch blocker:** inventory every personal-data field and processor, and verify the actual physical region for the primary database, backups, logs, analytics, support tooling and disaster-recovery copies. Railway region and provider subprocessors have not yet been independently evidenced as Kazakhstan-resident. If any required database/digital object is outside Kazakhstan, obtain specialist advice on compliant architecture and cross-border transfers; do not assume a foreign cloud region is compliant. Minimize data, define retention/deletion rules, record lawful consent/basis and processor agreements, restrict staff access, and document breach response.

## 5. Security and payment control requirements

Required before real-money-equivalent production activity:
- PostgreSQL in a verified compliant region, encrypted connections with certificate validation, encrypted backups, tested restore and least-privilege database roles.
- No secrets in source, logs, client bundles or support messages; rotate any exposed credentials.
- Telegram webhook secret verification, bounded request size, replay/idempotency protection and monitoring.
- Server-authoritative price/order state; only Telegram `successful_payment` proves payment. A pre-checkout approval or invoice-link click is not payment.
- Idempotent settlement and append-only double-entry journal; daily reconciliation against Telegram transaction history.
- Refund execution only via `refundStarPayment`; ambiguous timeout remains locked for manual reconciliation, never blind retry. Confirmed refund must be followed by a compensating ledger entry.
- Admin actions require strong authentication, least privilege, audit trail and ideally dual control for high-value refunds.
- Rate limits, abuse/fraud monitoring, dependency and secret scanning, SAST/DAST, penetration testing, load/concurrency tests, alerting and incident runbooks.
- No seller payout or escrow until a separately reviewed and legally approved model exists. A Stars payment receipt is not a seller payout.

## 6. Release decision checklist

The release manager must attach evidence and obtain explicit approval for every item:

- [ ] Written Kazakhstan legal opinion on NFT/gift classification and platform licensing perimeter.
- [ ] Written tax/accounting opinion for operator revenue, seller proceeds, fees, Stars and receipts.
- [ ] Consumer terms, seller agreement, privacy notice, dispute/refund policy in Kazakh and Russian.
- [ ] Confirmed personal-data map, Kazakhstan storage-region evidence, processor contracts and transfer assessment.
- [ ] Independent security review and penetration-test remediation.
- [ ] PostgreSQL migration, backup/restore, reconciliation and rollback evidence.
- [ ] Staging E2E evidence for invoice, pre-checkout, successful payment, delivery, duplicate update, refund, timeout and manual reconciliation.
- [ ] A documented seller payout model, if payouts are to be offered; otherwise seller balances remain liabilities and are not withdrawable.
- [ ] Explicit written production release approval.

Until all applicable boxes are evidenced, production payment and real-deal feature flags stay disabled. This document does not authorize deployment, migration, collection of Stars, refunds, wallet operations or movement of assets/funds.

## 7. Infrastructure audit snapshot (2026-11-13)

A read-only inspection of the currently configured Railway production environment found:

- The live backend service is still configured to deploy from GitHub branch `main`, not the draft payment/compliance branch.
- The backend and managed PostgreSQL service are configured in Railway region `iad` (US East / Ashburn). This is not Kazakhstan-resident storage.
- The backend's production variable-name list does not include `DATABASE_URL` or `GIFT_RADAR_STORAGE_BACKEND`; it does include `GIFT_RADAR_DB_PATH`. The live app therefore has not been demonstrated to use the provisioned PostgreSQL service for its financial/user records.
- The production backend still has a `PORTALS_API_KEY` variable configured, and the current `main` branch contains Portals references. The standalone-marketplace implementation exists in the unmerged draft PR branch and is not yet the live production version.
- Railway reports the backend, PostgreSQL and Redis services as online, but online infrastructure status does not establish compliance, correct database connectivity, or application readiness.

**Consequences:** production must not be treated as compliant with Kazakhstan personal-data localization; do not load real customer/payment data or enable checkout there. Do not remove the legacy Portals variable or change the live source/deploy configuration as part of this read-only audit, because doing so may affect the currently running service. A separately reviewed cutover plan must move the application and all in-scope data to a verified Kazakhstan-resident architecture, remove unused legacy integration secrets, connect and migrate to the intended database, and prove backup/restore and reconciliation before any live activation.


## 8. Agreed multi-rail product architecture (clarification)

The product requirement is **Stars plus crypto as an additional payment family**, not crypto replacing Stars. The agreed asset roadmap is:
- **Telegram Stars (XTR):** mandatory checkout rail for purchases of digital goods/services made inside the Telegram bot or Mini App.
- **USDT on TON:** planned additional crypto rail, subject to the legal and product-surface restrictions below. Only the canonical USDT TON Jetton master contract may be accepted after independently verifying its official address and decimals.
- **GRAM on TON:** planned additional crypto rail only after the exact token master contract, authenticity, decimals, liquidity/transfer semantics and legal classification are independently verified. A ticker/name match is not sufficient.
- **TRON / USDT-TRC20:** not part of the Telegram Mini App wallet/payment implementation. Do not accept it through a Mini App flow.

**Important Telegram boundary:** Telegram's current Bot Platform Developer Terms section 6.2 and Stars FAQ explicitly require all transactions for digital goods and services sold inside Telegram bots and Mini Apps to be paid exclusively in Stars. The FAQ explicitly says this remains true even if the operator also has a separate website or other external payment provider. Therefore, an additional crypto button, TON Connect payment request, QR, external checkout link, or redirect inside the Telegram bot/Mini App must not be used to collect crypto for a digital gift sale. This is not a matter that can be cured merely by obtaining a Kazakhstan legal opinion.

Official sources:
- https://telegram.org/tos/bot-developers (section 6.2 and 7)
- https://core.telegram.org/bots/payments-stars (FAQ: alternative currency, own website, cryptocurrency)
- https://docs.ton.org/applications/ton-connect/overview (wallet connection standard; not a payment-policy exception)

**Possible separate web product surface:** crypto checkout may be evaluated for a genuinely independent GIFT RADAR website/web marketplace, where the customer independently accesses that service and the transaction is not initiated, promoted, redirected or completed as an alternative payment path from the Telegram bot/Mini App. The same listing appearing on both surfaces does not automatically make the web transaction compliant; counsel must review the actual user journey, product parity, links, deep-links, marketing and platform interpretation. Do not assume this route is approved until written Telegram/platform and Kazakhstan legal review confirms it.

**Architecture to preserve without enabling live crypto:**
- One Payment Orchestrator with isolated adapters: `TelegramStarsAdapter`, `TonUsdtAdapter`, and `TonGramAdapter`. Rail eligibility is decided by server-side product-surface policy before an adapter can create a payment.
- A mandatory server-side rule: `surface=telegram_bot|telegram_mini_app AND item_type=digital_good => rail=telegram_stars_xtr_only`. This must be enforced by backend authorization, not just hidden UI controls.
- For an independently approved web surface only, crypto adapter eligibility must be separately configured and default-off. No cross-surface fallback or silent conversion.
- TON Connect may connect Telegram Wallet, Tonkeeper or MyTonWallet; it must never request seed phrases/private keys or hold signing authority. A wallet connection proves address control only.
- The chain observer verifies network, canonical token master contract, destination, amount, decimals, transaction finality, sender/recipient and unique transaction/log identifiers. Enforce exact invoice binding, replay prevention, idempotency, confirmations/finality policy and reconciliation.
- No custodial collection, exchange, swap, bridge, escrow or seller crypto payout unless separately designed and legally approved.
- Keep all crypto payment adapters disabled in production until the legal/product-surface gate is signed off. Keep Stars checkout disabled until the existing Stars release gates are passed.

This clarification supersedes any earlier design note suggesting that TON Connect could be used to accept GRAM or USDT directly inside the Telegram Mini App for the sale of digital gifts. TON Connect's technical support for a token transfer does not override Telegram's Stars-only rule for in-Telegram digital goods.


## 9. Competitive balance model and multi-asset account design (product decision, not launch authorization)

The product objective is to match the convenience users expect from specialist Telegram-gift marketplaces: deposit once, maintain an immediately visible account balance, buy/list/offer without signing a new wallet transaction for every internal trade, and withdraw through a clearly disclosed process. Public Portals documentation describes an internal GRAM balance with on-chain deposits/withdrawals and off-chain internal settlement; MRKT publicly describes gift deposits into platform storage and sale proceeds credited to an internal balance. These are competitor product patterns, not evidence that the same flow is approved for GIFT RADAR under Telegram terms or Kazakhstan law.

References reviewed:
- Portals custody and internal GRAM balance: https://docs.portals.tg/security/custody
- Portals top-up and purchase flow: https://docs.portals.tg/getting-started/quickstart
- MRKT marketplace balance flow: https://www.mrktxyz.com/en/faq/
- MRKT how-it-works: https://mrkt.info/en/how-it-works/

### 9.1 User experience target

Design one account/wallet screen with:
- A clear primary marketplace price unit: GRAM, where the asset/token and its value semantics have been verified.
- Visible asset-specific available, reserved, pending and withdrawable amounts. Do not silently merge unlike assets into one spendable number.
- A single transaction history showing deposits, purchases, sales, offers, fees, refunds, reversals and withdrawals with the original asset, network, amount, exchange quote (if any), timestamp and status.
- Deposit once and perform internal marketplace operations from the platform ledger, avoiding an on-chain wallet signature for every internal trade.
- Explicit, user-confirmed withdrawal destinations and network; no seed phrase/private key collection and no wallet signing authority held by GIFT RADAR.

### 9.2 Separate rails and accounting buckets

Implement the domain model so the product can support multiple payment methods without treating them as interchangeable money:

1. `GRAM` bucket: only credit after a confirmed deposit of the verified canonical GRAM asset on TON, or after another separately approved and fully accounted settlement event.
2. `USDT_TON` bucket: only credit after a confirmed deposit of the verified canonical USDT Jetton on TON. Keep the original liability denominated in USDT unless a legally approved conversion is explicitly executed and recorded.
3. `TELEGRAM_STARS_XTR` route: a direct Telegram Stars checkout for an eligible in-Telegram digital-goods purchase. A Stars payment must not be credited to a withdrawable GRAM/USDT balance, exchanged into crypto, or used as a disguised crypto-balance top-up.
4. Display GRAM as the marketplace reference/pricing unit where appropriate, but show any USDT equivalent as a live quote with expiry, rate source, spread/fee and rounding disclosed before confirmation. Do not silently convert a user's deposited USDT into GRAM.
5. Every bucket is a separate double-entry ledger liability with raw integer amounts, explicit asset/network/contract/decimals, idempotent event IDs and immutable audit events. Reservations for offers/orders are ledger holds, not transfers. A cancellation releases the exact hold; settlement debits and credits atomically.

A later product decision may choose a true GRAM-denominated internal balance backed by GRAM only. If USDT is converted into GRAM at deposit time, that is an exchange/conversion service and must be separately assessed, priced, authorized and reconciled; it must not be implemented as a mere UI display conversion.

### 9.3 Surface-specific checkout policy

The backend must distinguish the actual transaction surface and item classification, not merely the frontend hostname:

- Telegram bot or Mini App sale of a digital gift/digital good: Stars/XTR only, under the current Telegram Developer Terms and Stars FAQ. No crypto deposit button or alternative payment route may be used in that in-Telegram purchase journey.
- Standalone web marketplace: GRAM/USDT checkout or deposits may be evaluated only after written Kazakhstan legal review, provider/chain review, and a separate platform-policy review of the actual acquisition and navigation journey. The existence of a website alone is not sufficient. Do not use Telegram messages, buttons, deep links, QR codes or Mini App redirects to route a Telegram digital-goods buyer into a crypto checkout.
- A shared account or backend does not erase the surface distinction. Enforce server-side policy using authenticated surface, order origin, item type, session provenance and immutable order metadata. Reject ambiguous or missing provenance (fail closed).
- TON Connect is the wallet-connection/signing protocol for TON functionality in the Mini App. It does not itself authorize a crypto payment for an in-Telegram digital-goods sale.

### 9.4 Custody and settlement decision gate

A deposit into an address controlled by GIFT RADAR, followed by an internal balance credit, is a custodial or custody-like platform flow even if the user originally connected a self-custody wallet and GIFT RADAR never sees the user's seed phrase. The product must not describe this as non-custodial merely because wallet connection uses TON Connect.

Before implementing platform-controlled deposits, internal balances, gift vaults, escrow or withdrawals, obtain a written Kazakhstan opinion that specifically covers:
- custody/control of user crypto and deposited gifts;
- exchange/conversion between USDT and GRAM;
- operation of an internal transferable or withdrawable balance;
- marketplace order matching, escrow and settlement;
- licensing/authorization, AML/CFT, sanctions screening and customer due diligence;
- safeguarding/segregation of client assets, insolvency treatment, reconciliation and withdrawal obligations;
- data localization and processor/hosting requirements.

If the approved legal model does not permit platform custody, evaluate a non-custodial or third-party licensed custody/settlement design. Do not simulate a custodial balance in the UI while leaving the legal/technical custody model undefined.

### 9.5 Implementation order

- [ ] Build the multi-asset ledger abstraction and account UI in test/staging only; no live deposits.
- [ ] Add asset registry with independently verified GRAM and USDT TON master contracts, decimals and network identifiers. Until verified, asset status remains disabled.
- [ ] Implement deposit intent/address assignment, chain observer, finality policy, exact-amount checks, replay protection, reconciliation and withdrawal state machine behind default-off feature flags.
- [ ] Implement GRAM and USDT as separate asset buckets first. Add GRAM-denominated quote/display only; do not auto-convert deposits.
- [ ] Keep Stars as a separate direct XTR checkout adapter for eligible Telegram purchases. Never credit Stars to a withdrawable crypto balance.
- [ ] Add surface provenance and backend policy tests proving Telegram digital-goods purchases reject GRAM/USDT while eligible standalone web flows remain disabled until approval.
- [ ] Complete legal sign-off and independent security review before enabling any deposit, conversion, custody, escrow, withdrawal or real order.

This section records the intended competitive UX and accounting architecture. It does not authorize crypto acceptance, custody, conversion, live deposits, seller payouts, deployment or production feature-flag changes.
