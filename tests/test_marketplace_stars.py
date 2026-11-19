import json

import pytest

import marketplace_offers
from marketplace_payments import create_payment_intent, transition_deal_status
from marketplace_stars import confirm_stars_payment, validate_stars_pre_checkout


class StarsFlowTests:
    def _deal_with_intent(self, client):
        seller_headers = {"X-Telegram-Init-Data": "user-a"}
        buyer_headers = {"X-Telegram-Init-Data": "user-b"}
        listing = client.post(
            "/api/marketplace/orders", headers=seller_headers,
            json={"kind": "listing", "title": "Stars checkout gift", "price": "10", "currency": "XTR"},
        )
        assert listing.status_code == 201, listing.text
        offer = client.post(
            "/api/marketplace/offers", headers=buyer_headers,
            json={"order_id": listing.json()["item"]["id"], "amount": "9", "note": "Stars checkout"},
        )
        assert offer.status_code == 201, offer.text
        accepted = client.patch(
            f"/api/marketplace/offers/{offer.json()['item']['id']}",
            headers=seller_headers,
            json={"action": "accept"},
        )
        assert accepted.status_code == 200, accepted.text
        deal_id = accepted.json()["deal_id"]
        payload = f"hassle:stars:{deal_id}:test"
        with marketplace_offers._db() as conn:
            transition_deal_status(
                conn, deal_id=deal_id, new_status="awaiting_payment",
                actor_user_id="202", idempotency_key=f"{deal_id}:checkout-request",
                event_type="deal_payment_requested", event_data={"provider": "telegram_stars"},
                now=1700000000,
            )
            intent = create_payment_intent(
                conn, deal_id=deal_id, provider="telegram_stars", currency="XTR",
                amount="9", idempotency_key=f"{deal_id}:stars-intent",
                invoice_payload=payload, expires_at=1700000900,
                metadata={"buyer_telegram_id": "202", "deal_id": deal_id},
                now=1700000000,
            )
            conn.commit()
        return deal_id, payload, intent["id"]

    def test_stars_precheckout_binds_user_amount_and_deal_and_is_one_time(self, client):
        deal_id, payload, intent_id = self._deal_with_intent(client)
        ok, reason = validate_stars_pre_checkout(
            invoice_payload=payload, telegram_user_id="202",
            currency="XTR", total_amount=9, now=1700000010,
        )
        assert ok is True
        assert reason == ""
        with marketplace_offers._db() as conn:
            intent = conn.execute(
                "SELECT status FROM marketplace_payment_intents WHERE id=?", (intent_id,)
            ).fetchone()
            deal = conn.execute(
                "SELECT status FROM marketplace_deals WHERE id=?", (deal_id,)
            ).fetchone()
        assert intent["status"] == "pending"
        assert deal["status"] == "payment_pending"
        rejected, _ = validate_stars_pre_checkout(
            invoice_payload=payload, telegram_user_id="202",
            currency="XTR", total_amount=9, now=1700000011,
        )
        assert rejected is False

    def test_stars_successful_payment_confirms_once_and_posts_balanced_escrow_ledger(self, client):
        deal_id, payload, intent_id = self._deal_with_intent(client)
        ok, _ = validate_stars_pre_checkout(
            invoice_payload=payload, telegram_user_id="202",
            currency="XTR", total_amount=9, now=1700000010,
        )
        assert ok is True
        kwargs = {
            "invoice_payload": payload,
            "telegram_user_id": "202",
            "currency": "XTR",
            "total_amount": 9,
            "telegram_payment_charge_id": "tg-charge-001",
            "provider_payment_charge_id": "",
            "now": 1700000020,
        }
        confirmed = confirm_stars_payment(**kwargs)
        assert confirmed["deal_id"] == deal_id
        assert confirmed["status"] == "paid"
        replay = confirm_stars_payment(**kwargs)
        assert replay["status"] == "paid"
        with marketplace_offers._db() as conn:
            intent = conn.execute(
                "SELECT status, provider_reference FROM marketplace_payment_intents WHERE id=?",
                (intent_id,),
            ).fetchone()
            deal = conn.execute(
                "SELECT status FROM marketplace_deals WHERE id=?", (deal_id,)
            ).fetchone()
            tx_count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_ledger_transactions WHERE reference_id=?",
                (deal_id,),
            ).fetchone()[0]
            entries = conn.execute(
                "SELECT direction, amount FROM marketplace_ledger_entries WHERE transaction_id IN "
                "(SELECT id FROM marketplace_ledger_transactions WHERE reference_id=?)",
                (deal_id,),
            ).fetchall()
            funded = conn.execute(
                "SELECT COUNT(*) FROM marketplace_escrow_events WHERE deal_id=? AND event_type='escrow_funded'",
                (deal_id,),
            ).fetchone()[0]
        assert intent["status"] == "confirmed"
        assert intent["provider_reference"] == "tg-charge-001"
        assert deal["status"] == "paid"
        assert tx_count == 1
        assert len(entries) == 2
        assert funded == 1
