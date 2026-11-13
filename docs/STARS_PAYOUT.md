# Telegram Stars: receiving, availability and rewards withdrawal

**Scope:** research note for a future optional Stars integration. No Stars code is part of the TON/USDT testnet vertical slice.

## 1. Receiving Stars

For digital goods and services sold by a bot or Mini App inside Telegram, the Bot Payments API requires currency `XTR` (Telegram Stars). The bot sends an invoice, answers `pre_checkout_query` within 10 seconds, and must wait for the `successful_payment` update before treating the order as paid. Persist `telegram_payment_charge_id` for future refund handling.

Telegram's Bot API does not expose a general seller withdrawal method. Successful Stars purchases accrue to the bot's Stars balance, visible to the Telegram account that owns the bot.

Official docs:
- https://core.telegram.org/bots/payments-stars
- https://core.telegram.org/bots/api#successfulpayment
- https://core.telegram.org/bots/api#refundstarpayment

## 2. When Stars become available

Telegram's Bot Platform Developer Terms say that, for technical reasons, Stars may not become available for advertising credits or rewards for **up to 21 days** after receipt. This is a maximum stated availability delay, not a guaranteed payout date.

Telegram may temporarily withhold or permanently debit Stars in response to user refunds, suspected abuse, or violations of platform terms. Stars earned by a bot are valid for **3 years** from receipt; unused balance not converted to rewards or spent on ads within that period is forfeited.

The current revenue status includes:
- `current_balance`
- `available_balance`
- `overall_revenue`
- optional `next_withdrawal_at`
- `withdrawal_enabled`

Do not calculate spendable/withdrawable funds from gross successful-payment totals. Use Telegram's current available balance and withdrawal eligibility.

Official terms:
- https://telegram.org/tos/bot-developers (sections 6.2.2, 6.2.4, 6.2.7)

## 3. Rewards withdrawal route

Telegram currently outsources rewards processing to Fragment. The documented MTProto flow is:

1. The **owner** of the bot/channel requests the revenue-withdrawal URL using `payments.getStarsRevenueWithdrawalUrl`.
2. The request requires the owner's Telegram 2-step verification password, represented through Telegram's `InputCheckPasswordSRP` flow.
3. Withdrawal is available only when the bot/channel balance meets the current `stars_revenue_withdrawal_min` threshold and `withdrawal_enabled=true`.
4. The returned URL opens a Fragment page. The owner specifies and submits a TON wallet address there.
5. `stars_revenue_withdrawal_max` caps each withdrawal; to withdraw more, the owner repeats the process.
6. Telegram documents the reward conversion rate as the current `stars_usd_withdraw_rate_x1000` client configuration value for 1,000 Stars. The terms currently describe a developer reward equivalent of **USD 0.013 per Star**, but Telegram may change its assigned value.

This is an owner-operated Fragment withdrawal process. It is **not** an API for paying arbitrary marketplace sellers, not a Stars-to-user wallet transfer, and not an escrow payout rail. Do not promise a seller that their share can be withdrawn directly from the bot's Stars balance.

Official API reference:
- https://core.telegram.org/api/stars (sections “Revenue statistics” and “Withdrawing revenue”)
- https://telegram.org/tos/bot-developers (sections 6.2.4 and 6.2.4.1)
- https://fragment.com/ (Fragment terms and regional eligibility apply)

## 4. Refund and dispute exposure

The bot operator is responsible for handling legitimate digital-goods disputes and refunds. Stars refunds are issued using Bot API `refundStarPayment`, with the user ID and stored `telegram_payment_charge_id`.

A refund can debit the bot's Stars balance. Telegram explicitly discourages real-time sales of digital products and virtual items with inherent value that cannot be reclaimed, frozen, rescinded, invalidated or suspended after a successful dispute or chargeback.

The Mini App must support `/paysupport`. The platform must maintain a dispute/refund process and must not treat an internal ledger entry as a completed seller payout.

## 5. Product and legal implications for GIFT RADAR

- Stars can be a future checkout rail for digital goods sold **inside Telegram**, because this is the payment method Telegram requires for those sales.
- Stars rewards withdrawal is to a TON wallet through Fragment under the bot owner's account and eligibility. It does not establish a legal or technical mechanism for distributing proceeds to individual NFT sellers.
- The operator's country, Fragment eligibility, local tax treatment, custody characterization, consumer obligations and seller settlement model require specialist legal review before any live Stars flow.
- Keep Stars behind a disabled feature flag until a compliant seller settlement mechanism and legal review are approved.
- This note does not authorize real payments, Stars checkout, withdrawal automation or mainnet activity.

## 6. Decision for the current testnet release

No Stars checkout, webhook handler, signer attestation, refund handler or payout code will be added to the current TON/USDT testnet vertical slice. The Stars module remains an isolated future integration behind a disabled flag. The immediate engineering work is direct testnet TON and test Jetton escrow custody.

## Important Telegram checkout restriction

Telegram's current Bot Payments documentation and Developer Terms state that digital goods and services sold inside Telegram bots/Mini Apps must be paid exclusively in Stars. Cryptocurrency or third-party payment methods are not permitted for those digital-goods purchases inside Telegram apps. Therefore, direct TON/USDT escrow payments in this testnet branch are a **technical blockchain test only**, not an approved Telegram Mini App checkout design. A live product must either use Stars inside Telegram or obtain a separate reviewed distribution/payment model outside the Telegram digital-goods checkout flow; it must not route users around Telegram's restriction.

Official references:
- https://core.telegram.org/bots/payments-stars (FAQ: different currency/payment provider; cryptocurrency)
- https://telegram.org/tos/bot-developers (section 6.2)
