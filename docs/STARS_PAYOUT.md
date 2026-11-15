# Telegram Stars: receiving, refunds and developer rewards

## Receiving Stars

Digital goods and services sold inside a Telegram bot or Mini App must be paid for in Telegram Stars (XTR). The bot sends an invoice, answers `pre_checkout_query` within Telegram's required time limit, and waits for the authenticated `successful_payment` update before treating an order as paid. Persist the unique `telegram_payment_charge_id` for refund handling.

Official documentation:
- https://core.telegram.org/bots/payments-stars
- https://core.telegram.org/bots/api#successfulpayment
- https://core.telegram.org/bots/api#refundstarpayment

## Availability and rewards withdrawal

Stars may be withheld or debited for refunds, suspected abuse or terms violations. Reward availability can be delayed. The bot owner's Fragment rewards-withdrawal flow is not a general-purpose API for paying arbitrary marketplace sellers and must not be presented as seller payout functionality.

Use Telegram's current revenue and withdrawal eligibility data. Never infer withdrawable funds from gross successful-payment totals.

Official terms:
- https://telegram.org/tos/bot-developers
- https://core.telegram.org/api/stars
- https://core.telegram.org/method/payments.getStarsRevenueWithdrawalUrl
- https://fragment.com/

## Refund and dispute exposure

The operator is responsible for legitimate digital-goods disputes and refunds. Refunds use Telegram's `refundStarPayment` API with the buyer ID and stored Telegram charge ID. Ambiguous API outcomes must be reconciled against Telegram transaction history before another attempt.

The Mini App must provide `/paysupport`, clear terms and a documented dispute process. An internal ledger entry is not a completed seller payout.

## GIFT RADAR policy

- Stars (XTR) is the only in-Mini-App purchase currency.
- TON Connect is used for TON wallet and NFT interactions, not as a substitute checkout for digital goods.
- Stars reward withdrawal to the bot owner's wallet does not automatically provide a mechanism for distributing proceeds to individual NFT sellers.
- Kazakhstan legal, tax, consumer-protection and seller-settlement review is required before real payment activation.
