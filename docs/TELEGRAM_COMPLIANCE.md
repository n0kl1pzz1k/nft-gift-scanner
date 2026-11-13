# Telegram compliance: payment routes for the NFT marketplace

**Checked:** 2026-11-14  
**Scope:** Telegram Bot Platform, Mini Apps, digital NFT/gift marketplace, TON and USDT payment routing.  
**Status:** Primary-source review, not a legal opinion. This document does not authorize mainnet or real-money operation.

> **Reading rule:** each conclusion below is tied to an official Telegram source. Where Telegram's published material does not expressly classify the exact marketplace/escrow structure, the conclusion is marked **Not confirmed**. Kazakhstan and other applicable legal questions require review by qualified counsel.

## 1. Primary sources

1. [Telegram Bot Platform Developer Terms of Service](https://telegram.org/tos/bot-developers), especially §§5.2, 6.1, 6.2, 6.3, 6.4 and 7.
2. [Bot Payments API for Digital Goods and Services](https://core.telegram.org/bots/payments-stars).
3. [Telegram Blockchain Guidelines](https://core.telegram.org/bots/blockchain-guidelines).
4. [Telegram Bot API](https://core.telegram.org/bots/api), including `getMyStarBalance`, `getStarTransactions` and `refundStarPayment`.
5. [Telegram Stars: balance, revenue statistics and withdrawal](https://core.telegram.org/api/stars).
6. [MTProto: payments.getStarsRevenueWithdrawalUrl](https://core.telegram.org/method/payments.getStarsRevenueWithdrawalUrl).
7. [Terms of Service for Mini Apps](https://telegram.org/tos/mini-apps).

Only Telegram-owned documentation and Telegram's own terms are used for Telegram-rule conclusions in this document. No third-party blog or community interpretation is treated as authority.

## 2. What the primary sources say

### 2.1 Digital goods and virtual items

Telegram's Bot Developer Terms say that transactions pertaining to digital goods and services offered by a third-party app (bot or Mini App) must be executed exclusively through Telegram Stars. The terms say that using alternative payment systems for those sales can result in a notice and, if not corrected, the bot/Mini App becoming inaccessible from Telegram store clients or being terminated from the Bot Platform.  
Source: [Bot Developer Terms, §6.2](https://telegram.org/tos/bot-developers#6-2-digital-goods-and-services); [Payments API FAQ](https://core.telegram.org/bots/payments-stars#faq).

The Payments API FAQ expressly says that having a separate website or other payment provider does not remove the Stars requirement for digital goods and services sold inside Telegram apps.  
Source: [Payments API FAQ](https://core.telegram.org/bots/payments-stars#faq).

The Terms also say that the seller is responsible for timely delivery and handling payment disputes. The bot/Mini App must support `/paysupport`. Telegram may deduct refunded Stars from the TPA balance and expressly discourages real-time sales of digital products or virtual items with inherent value that cannot be reclaimed, frozen, rescinded, invalidated or suspended after a successful dispute or chargeback.  
Source: [Bot Developer Terms, §6.2.1](https://telegram.org/tos/bot-developers#6-2-1-payment-disputes-for-digital-goods-and-services).

**Application to this marketplace:** a purchase of a digital NFT/collectible gift through the Mini App appears to fall within the digital-goods/virtual-items category. Telegram's sources do not define our exact P2P escrow model or explicitly classify every kind of on-chain collectible transfer. Treating the listing as a P2P transfer rather than a platform sale does not create an express exception in the published text. Final classification of the precise contractual structure is **not confirmed** and requires written legal review.

### 2.2 Blockchain and TON

For Mini Apps and their connected bots, Telegram's Developer Terms require cryptocurrency functionality to be based exclusively on TON. Wallet interaction must use TON Connect SDK, including wallet authorization and transaction signing.  
Source: [Bot Developer Terms, §7](https://telegram.org/tos/bot-developers#7-blockchain-integration).

Telegram's Blockchain Guidelines say TON-based tokens and collectible NFTs are permitted examples. They prohibit issuing/distributing assets on other blockchains, using non-TON wallet connection protocols except for bridging, promoting non-TON cryptoassets/wallets, and linking to external platforms where non-TON cryptoassets are promoted or used. The guidelines clarify that these blockchain rules apply to Mini Apps and their bots; regular bots without a Mini App component are exempt from those particular §7 rules.  
Source: [Blockchain Guidelines](https://core.telegram.org/bots/blockchain-guidelines).

**Important distinction:** permission to integrate TON blockchain functionality is not, by itself, permission to use TON or a TON Jetton as the checkout currency for a digital good. The separate Stars rule in §6.2 and the Payments API FAQ still apply to digital goods sold inside Telegram apps.  
Sources: [Bot Developer Terms, §§6.2 and 7](https://telegram.org/tos/bot-developers); [Payments API FAQ](https://core.telegram.org/bots/payments-stars#faq).

### 2.3 External website and Mini App responsibility

Telegram describes Mini Apps as third-party services operated by their providers. Telegram says it does not itself process payments or hold funds in Mini Apps; payment processing is performed by third-party providers selected by the service provider. The service provider is responsible for its service and disputes.  
Source: [Mini App Terms, §§2–3](https://telegram.org/tos/mini-apps).

Bot Developer Terms also prohibit using the Bot Platform with external interfaces/frontends to develop external services or applications that diverge significantly from the intended use cases of the Bot Platform.  
Source: [Bot Developer Terms, §5.2(e)](https://telegram.org/tos/bot-developers#5-2-code-of-conduct).

The FAQ confirms that a website does not exempt digital goods sold inside Telegram apps from the Stars requirement. Whether a separately operated website may sell the same digital assets in an entirely off-Telegram transaction, and what degree of linking or account integration is acceptable for this particular marketplace, is **not confirmed** by the cited Telegram sources.  
Sources: [Payments API FAQ](https://core.telegram.org/bots/payments-stars#faq); [Bot Developer Terms, §5.2(e)](https://telegram.org/tos/bot-developers#5-2-code-of-conduct).

## 3. Three payment routes

### Route A. Telegram Stars checkout inside the Mini App

**Rule position**

- For digital goods/services sold through the bot or Mini App, Stars-only is the explicit Telegram rule. This is the clearest documented route for an in-Mini-App sale of a digital NFT/collectible.  
  Sources: [§6.2 Bot Developer Terms](https://telegram.org/tos/bot-developers#6-2-digital-goods-and-services); [Payments API FAQ](https://core.telegram.org/bots/payments-stars#faq).
- The bot sends an invoice with currency `XTR`, handles `pre_checkout_query`, and must wait for `successful_payment` before delivery. The `telegram_payment_charge_id` should be retained for refunds.  
  Source: [Payments API implementation flow](https://core.telegram.org/bots/payments-stars#implementing-payments).
- `pre_checkout_query` must be answered within 10 seconds. A pre-checkout approval is not proof of successful payment.  
  Source: [Payments API pre-checkout and checkout](https://core.telegram.org/bots/payments-stars#step-by-step-process).
- The seller/provider must handle `/paysupport` and refunds. Telegram warns against real-time delivery of virtual items that cannot be reclaimed after a successful dispute/chargeback.  
  Source: [§6.2.1 Bot Developer Terms](https://telegram.org/tos/bot-developers#6-2-1-payment-disputes-for-digital-goods-and-services).

**Risks**

- Stars can be refunded; the corresponding amount is debited from the TPA balance. A delivered NFT may be technically irreversible or difficult to recover.  
  Source: [§6.2.1](https://telegram.org/tos/bot-developers#6-2-1-payment-disputes-for-digital-goods-and-services).
- Stars may be withheld or debited for refunds, suspected abuse or terms violations. Reward availability can be delayed up to 21 days.  
  Source: [§6.2.4](https://telegram.org/tos/bot-developers#6-2-4-rewards-for-stars).
- Stars are not the developer's property and Telegram says it is not a financial institution or custodian. Fragment may be unavailable in some countries or to some users.  
  Source: [§§6.2.2 and 6.2.4.1](https://telegram.org/tos/bot-developers#6-2-2-tpa-balance).
- The Terms state a reward equivalent of USD 0.013 per Star, but this is a Telegram-assigned reward value, not a guaranteed fiat exchange rate or a seller payout promise.  
  Source: [§6.2.4](https://telegram.org/tos/bot-developers#6-2-4-rewards-for-stars).

**Architecture impact**

- Keep Stars in a separate payment adapter/module behind a feature flag. Do not couple it to the TON/Jetton escrow contract.
- Model invoice, pre-checkout, successful payment, refund, charge ID, available/withheld balance and reward withdrawal as separate states.
- Do not treat a Stars payment as final settlement to the seller. Maintain a reserve/contingency policy for refunds and chargebacks.
- NFT release delay alone does not eliminate later refund risk. The service terms and contract must define who bears the loss if Stars are refunded after irreversible NFT delivery.
- Provide `/paysupport`, clear terms acceptance, support contact and payment records.

**Conclusion:** This is the documented route for digital goods sold inside Telegram. It still carries material refund and seller-settlement risk. It is not a legal opinion on whether this specific NFT product is eligible.

### Route B. TON/USDT P2P escrow from the Mini App

**Rule position**

- TON is the required blockchain for cryptocurrency functionality in Mini Apps; TON Connect is the required wallet interface.  
  Sources: [§7 Bot Developer Terms](https://telegram.org/tos/bot-developers#7-blockchain-integration); [Blockchain Guidelines](https://core.telegram.org/bots/blockchain-guidelines).
- A TON-based collectible/NFT and TON wallet interactions are shown as permitted examples under the blockchain guidelines.  
  Source: [Blockchain Guidelines, Blockchain Assets and TON Connect](https://core.telegram.org/bots/blockchain-guidelines).
- However, the Stars-only rule for digital goods is separate. Telegram's Payments API FAQ says cryptocurrency cannot replace Stars for digital goods/services sold inside bots and Mini Apps.  
  Source: [Payments API FAQ](https://core.telegram.org/bots/payments-stars#faq).
- Whether a genuinely user-to-user P2P transaction with a neutral smart-contract escrow, where the Mini App facilitates discovery and escrow but is not the contractual seller, is classified differently is **not confirmed** by Telegram's published sources. No exception for P2P escrow is stated in the cited rules.

**Risks**

- If Telegram classifies the flow as a digital-goods sale by/through the TPA, TON/USDT checkout may conflict with §6.2 even though the escrow itself is on TON.
- If any wallet integration is not TON Connect, or any asset/marketing/link points to non-TON cryptoassets, there are additional §7 risks.
- The service provider remains responsible for its service and user disputes; Telegram does not guarantee or oversee the escrow or transaction.  
  Source: [Mini App Terms, §§2–3](https://telegram.org/tos/mini-apps).
- Smart-contract, Jetton, oracle, sanctions/AML, custody and consumer-protection questions are separate legal/technical risks and are not resolved by Telegram's blockchain guidelines.

**Architecture impact**

- Keep this route as a separate TON testnet technical vertical slice, not as an enabled Mini App checkout.
- Use TON Connect for user wallet interaction in a Mini App integration. Keep asset/network allowlists and verify the Jetton master address; a platform-issued test Jetton is not official Tether USDT.
- Keep escrow payment confirmation, NFT custody and ownership proof on-chain and independently reconciled. Do not route funds through Stars or describe Stars as the escrow asset.
- Do not enable a real-money route inside the Mini App until Telegram's classification is confirmed in writing and counsel has reviewed the marketplace contract and jurisdictional rules.

**Conclusion:** TON-based blockchain functionality is documented, but TON/USDT payment for an NFT sale inside the Mini App is not established as permitted by those blockchain rules. The Stars-only digital-goods rule is the principal obstacle. P2P-specific exception: **not confirmed**.

### Route C. Hybrid: Telegram Mini App for discovery/account, external website for checkout

**Rule position**

- Telegram expressly says that operating a separate website does not remove the Stars requirement for digital goods sold inside Telegram apps.  
  Source: [Payments API FAQ](https://core.telegram.org/bots/payments-stars#faq).
- The cited FAQ does not expressly say that every sale completed wholly outside Telegram must use Stars. That off-platform case is **not confirmed** by the FAQ itself.
- The Bot Developer Terms prohibit using the Bot Platform together with external frontends to develop external services that diverge significantly from intended Bot Platform use cases.  
  Source: [§5.2(e) Bot Developer Terms](https://telegram.org/tos/bot-developers#5-2-code-of-conduct).
- Mini Apps must not promote non-TON cryptoassets or direct/link users to external platforms where non-TON cryptoassets are promoted or used.  
  Source: [§7.4 Bot Developer Terms](https://telegram.org/tos/bot-developers#7-4-promotion); [Blockchain Guidelines](https://core.telegram.org/bots/blockchain-guidelines).

**Risks**

- Moving the payment button to a website does not automatically make the flow compliant if the Mini App is still effectively selling the digital NFT inside Telegram or is used to route users around the Stars requirement.
- A website flow involving TON-based assets is not expressly prohibited by the cited §7.4 non-TON rule, but that does not amount to affirmative Telegram approval of this business model.
- Linking to external venues that promote/use non-TON cryptoassets creates a specific §7.4 issue.
- The boundary between a Mini App as an account/discovery interface and an external website as a separate marketplace is not precisely defined for this use case in the sources reviewed.

**Architecture impact**

- Separate the web marketplace checkout and the Telegram Mini App presentation/authentication surfaces. Keep one authoritative backend order state and an auditable handoff, but do not assume a link-out makes the transaction exempt from Telegram rules.
- Do not put a crypto checkout CTA, non-TON wallet promotion or links to non-TON crypto platforms inside the Mini App.
- Keep the external website independently usable, with its own terms, support, payment provider and legal disclosures. Whether this separation is sufficient must be reviewed before release.
- Obtain written Telegram clarification and jurisdiction-specific legal advice before activating a crypto checkout reachable from the Mini App.

**Conclusion:** A separate website is not a blanket workaround. In-app digital sales remain Stars-only. A truly off-Telegram sale may be a distinct case, but Telegram's sources reviewed here do not conclusively classify this hybrid NFT marketplace. **Not confirmed; legal review required.**

## 4. Stars: receipt, balance, refund and withdrawal

### 4.1 Receiving Stars

For digital goods, the official Bot API flow is:
1. Send an invoice with currency `XTR`.
2. Receive `pre_checkout_query` and answer it within 10 seconds.
3. Wait for `successful_payment`; do not deliver based only on pre-checkout.
4. Store `telegram_payment_charge_id` for possible refunds.
5. Deliver the purchased item/service and provide payment support.

Sources: [Payments API](https://core.telegram.org/bots/payments-stars#implementing-payments); [step-by-step flow](https://core.telegram.org/bots/payments-stars#step-by-step-process).

### 4.2 What the bot can inspect and refund

The Bot API exposes:
- `getMyStarBalance` to read the bot's current Stars balance;
- `getStarTransactions` to retrieve transaction history;
- `refundStarPayment` to refund a successful Stars payment using the user's ID and `telegram_payment_charge_id`.

Source: [Telegram Bot API](https://core.telegram.org/bots/api#payments).

The Bot API documentation reviewed here does not expose a direct method for sending the bot's Stars balance to a seller's wallet. **Direct bot-to-seller payout through Bot API: not confirmed / no such method documented.**

### 4.3 Availability and withholding

- Stars may not become available for advertising credits or rewards for up to 21 days after receipt. This is an availability window, not a guaranteed exact settlement date.  
  Source: [§6.2.4 Bot Developer Terms](https://telegram.org/tos/bot-developers#6-2-4-rewards-for-stars).
- Telegram may temporarily withhold or permanently debit Stars due to user refunds, suspected abuse or terms violations.  
  Source: [§6.2.4](https://telegram.org/tos/bot-developers#6-2-4-rewards-for-stars).
- Stars remain valid for 3 years from receipt; unused Stars not converted to rewards or used for Telegram Ads are forfeited/debited after that period.  
  Source: [§6.2.7](https://telegram.org/tos/bot-developers#6-2-7-expiration).
- The Stars revenue status exposes current balance, available balance, overall revenue, withdrawal-enabled state and next withdrawal time.  
  Source: [Telegram Stars revenue statistics](https://core.telegram.org/api/stars#revenue-statistics).

### 4.4 Withdrawal by the bot owner

Telegram's published flow is not a normal Bot API payout call:
- The owner of the bot/channel requests a withdrawal URL using MTProto method `payments.getStarsRevenueWithdrawalUrl`.
- The method requires the owner account's 2FA password and is available only to users, not to a bot token acting autonomously.
- Withdrawal requires the configured minimum balance and `withdrawal_enabled` status. Telegram publishes dynamic configuration values for minimum and maximum withdrawal amounts; there is no fixed universal amount stated in the method page.
- The returned URL opens a Fragment page where the owner specifies the TON wallet address.
- The method documentation lists 2FA/session restrictions: 2FA must be enabled; password changes and newly created sessions may need to be at least 24 hours old.

Sources: [Telegram Stars: Withdrawing revenue](https://core.telegram.org/api/stars#withdrawing-revenue); [MTProto method](https://core.telegram.org/method/payments.getStarsRevenueWithdrawalUrl).

Telegram's Bot Developer Terms state a reward equivalent of USD 0.013 per Star, subject to Telegram's assigned value and possible changes. Fragment processes the rewards; it may be unavailable for some users or countries. Telegram does not promise alternative compensation if the owner cannot use Fragment or receive the reward.  
Source: [Bot Developer Terms, §§6.2.4–6.2.4.1](https://telegram.org/tos/bot-developers#6-2-4-rewards-for-stars).

**Implications for our marketplace**
- Stars revenue is held in the bot owner's Telegram balance, not in the on-chain escrow.
- Seller settlement cannot be represented as automatic just because the bot received Stars or the backend wrote a Ledger entry.
- Any seller payout would require a separately designed, legally reviewed payout arrangement. The Telegram sources do not document an API for splitting a Stars purchase among P2P sellers.
- The bot owner must account for 21-day availability delays, refund exposure, Fragment eligibility, dynamic withdrawal limits, 2FA/session constraints and the 3-year expiry.

## 5. Required service-terms language: dispute resolver multisig

The escrow dispute resolver must be described as an **independent, preconfigured multisignature custody mechanism**, not as one person or one wallet operator.

Suggested plain-language clause for counsel to adapt:

> If a dispute remains unresolved by the stated final deadline, the NFT may be transferred from the platform escrow to the custody address of the independent dispute-resolution multisignature. That address is controlled by a predefined quorum of independent authorized signers under published governance rules. No single signer can unilaterally decide the dispute or move the NFT. The multisignature's role is limited to temporary neutral custody and execution of a documented final direction under the service terms; it is not the buyer, seller, platform operator or a guarantee of payment. The custody handoff does not itself constitute delivery to the buyer, return to the seller, or payment to either party. The applicable quorum, signer replacement process, response deadline, appeal route, fees, governing law and effect of a final direction must be stated before the user enters the transaction.

**Not yet confirmed/implemented in code:** the resolver multisig contract, signer governance, quorum enforcement, emergency replacement, custody handoff and recovery tests. A configured address alone is not proof that it is a multisig. This clause is a drafting starting point only and requires legal review and an independently audited implementation.

## 6. Current engineering decision and gates

1. Continue TON testnet escrow testing as a technical experiment. Testnet TON and a platform-issued test Jetton do not establish permission for a live Mini App checkout.
2. Keep Stars code out of the current payment vertical slice. The Stars adapter remains a separately gated module and is not activated.
3. Do not implement a Mini App payment route until the owner selects one after legal/Telegram review.
4. Before any live Mini App sale of digital NFTs, resolve the Stars-only rule and the irreversible-delivery/refund risk.
5. Before any TON/USDT Mini App checkout, obtain a written classification of the exact P2P escrow structure from Telegram or qualified counsel. Blockchain support under §7 alone is insufficient.
6. Before a hybrid launch, define the real boundary between Telegram discovery/authentication and the external marketplace, then obtain written legal review of the linking and checkout flow.
7. Mainnet, real funds and production payment activation remain prohibited.

## 7. Legal review questions

Counsel should answer these questions against the actual marketplace contracts, user journey and Kazakhstan law:
- Is the platform the seller, agent, broker, escrow provider, or a combination for each NFT/gift transaction?
- Does a user-to-user sale of an on-chain digital collectible, with platform-operated smart-contract escrow, count as a digital-goods transaction offered or facilitated by a TPA under §6.2?
- Can the Mini App display listings and account/order state while a separate website independently executes the sale, without becoming a route around §6.2 or an external service caught by §5.2(e)?
- What disclosures, licensing, AML/sanctions, consumer, tax, custody and dispute-resolution obligations apply in Kazakhstan and in users' countries?
- What exact legal entity receives Stars, bears refund liability and owes seller proceeds? Is any Stars-based seller payout model contractually and technically available?
- Is the proposed multisignature custodian independent in law and operation, and what happens if its signers are unavailable or disagree?

Until those questions are answered, no route in this document should be described as legally approved for real-money launch.
