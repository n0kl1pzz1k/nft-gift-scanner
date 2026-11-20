"""Telegram Stars checkout validation and settlement callbacks.

Checkout is deliberately feature-gated at the API boundary. Provider callbacks
are verified against a one-time invoice payload, the Telegram user, currency,
amount, deal reservation and payment-intent state before any ledger mutation.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from database import begin_write, select_for_update
from marketplace_payments import (
    PaymentConflict,
    PaymentStateError,
    append_escrow_event,
    get_payment_intent_by_invoice_payload,
    post_ledger_transaction,
    record_payment_event,
    transition_deal_status,
    transition_payment_intent,
)


def validate_stars_pre_checkout(
    *,
    invoice_payload: str,
    telegram_user_id: str | int,
    currency: str,
    total_amount: int,
    now: int | None = None,
) -> tuple[bool, str]:
    """Atomically reserve one Stars invoice for its intended buyer."""
    from marketplace_offers import _db

    timestamp = int(time.time()) if now is None else int(now)
    user_id = str(telegram_user_id)
    try:
        with _db() as conn:
            intent = get_payment_intent_by_invoice_payload(conn, invoice_payload)
            metadata = json.loads(intent.get("metadata_json") or "{}")
            if (
                intent["provider"] != "telegram_stars"
                or intent["currency"] != "XTR"
                or currency != "XTR"
                or int(intent["amount"]) != int(total_amount)
                or str(metadata.get("buyer_telegram_id")) != user_id
                or int(intent["expires_at"]) <= timestamp
                or intent["status"] != "created"
            ):
                return False, "This payment request is invalid, expired, or already used."
            deal_id = str(intent["deal_id"])
            begin_write(conn, f"deal:{deal_id}", f"payment-intent:{intent['id']}")
            locked = conn.execute(
                select_for_update(conn, """SELECT d.*, o.status AS listing_status, o.currency AS listing_currency
                    FROM marketplace_deals d
                    JOIN marketplace_orders o ON o.id = d.order_id
                    WHERE d.id = ?"""),
                (deal_id,),
            ).fetchone()
            if (
                locked is None
                or str(locked["offer_sender_id"]) != user_id
                or locked["currency"] != "XTR"
                or locked["listing_status"] != "matched"
                or locked["listing_currency"] != "XTR"
                or locked["status"] != "awaiting_payment"
            ):
                conn.rollback()
                return False, "The deal is no longer available for payment."
            transition_payment_intent(conn, intent["id"], "pending", now=timestamp)
            transition_deal_status(
                conn,
                deal_id=deal_id,
                new_status="payment_pending",
                actor_user_id=user_id,
                idempotency_key=f"{deal_id}:stars-precheckout:{intent['id']}",
                event_type="payment_pending",
                event_data={"provider": "telegram_stars", "payment_intent_id": intent["id"]},
                now=timestamp,
            )
            conn.commit()
        return True, ""
    except (PaymentStateError, PaymentConflict, ValueError, KeyError, TypeError):
        return False, "This payment request could not be verified."


def confirm_stars_payment(
    *,
    invoice_payload: str,
    telegram_user_id: str | int,
    currency: str,
    total_amount: int,
    telegram_payment_charge_id: str,
    provider_payment_charge_id: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Confirm a Telegram Stars charge exactly once and post balanced escrow entries."""
    from marketplace_offers import _db

    timestamp = int(time.time()) if now is None else int(now)
    user_id = str(telegram_user_id)
    charge_id = str(telegram_payment_charge_id or "").strip()
    if not charge_id:
        raise PaymentStateError("Telegram payment charge ID is required")
    with _db() as conn:
        intent = get_payment_intent_by_invoice_payload(conn, invoice_payload)
        metadata = json.loads(intent.get("metadata_json") or "{}")
        if (
            intent["provider"] != "telegram_stars"
            or intent["currency"] != "XTR"
            or currency != "XTR"
            or int(intent["amount"]) != int(total_amount)
            or str(metadata.get("buyer_telegram_id")) != user_id
        ):
            raise PaymentConflict("Successful payment does not match its invoice")
        deal_id = str(intent["deal_id"])
        begin_write(conn, f"deal:{deal_id}", f"payment-intent:{intent['id']}")
        locked = conn.execute(
            select_for_update(conn, """SELECT d.*, o.status AS listing_status
                FROM marketplace_deals d
                JOIN marketplace_orders o ON o.id = d.order_id
                WHERE d.id = ?"""),
            (deal_id,),
        ).fetchone()
        if locked is None or str(locked["offer_sender_id"]) != user_id:
            conn.rollback()
            raise PaymentStateError("Payment deal or buyer is no longer valid")
        # A successful_payment update may be retried after settlement, but it
        # must never revive an intent/deal that was cancelled, expired, or
        # otherwise moved out of the payment-pending state.
        intent_status = str(intent["status"])
        deal_status = str(locked["status"])
        if intent_status not in {"pending", "confirmed"} or deal_status not in {"payment_pending", "paid"}:
            conn.rollback()
            raise PaymentStateError("Stars payment is not attached to an active payment-pending deal")
        if locked["listing_status"] != "matched":
            conn.rollback()
            raise PaymentStateError("The reserved listing is no longer available for this payment")
        payload_hash = hashlib.sha256(json.dumps({
            "invoice_payload": invoice_payload,
            "telegram_user_id": user_id,
            "currency": currency,
            "total_amount": int(total_amount),
            "telegram_payment_charge_id": charge_id,
            "provider_payment_charge_id": str(provider_payment_charge_id or ""),
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        record_payment_event(
            conn,
            provider="telegram_stars",
            provider_event_id=charge_id,
            payment_intent_id=intent["id"],
            event_type="successful_payment",
            payload_hash=payload_hash,
            now=timestamp,
        )
        transition_payment_intent(
            conn,
            intent["id"],
            "confirmed",
            provider_reference=charge_id,
            now=timestamp,
        )
        transition_deal_status(
            conn,
            deal_id=deal_id,
            new_status="paid",
            actor_user_id=user_id,
            idempotency_key=f"{deal_id}:stars-payment-confirmed:{intent['id']}",
            event_type="payment_confirmed",
            event_data={
                "provider": "telegram_stars",
                "payment_intent_id": intent["id"],
                "provider_reference": charge_id,
                "currency": "XTR",
                "amount": str(intent["amount"]),
            },
            now=timestamp,
        )
        ledger = post_ledger_transaction(
            conn,
            idempotency_key=f"{deal_id}:stars-escrow:{charge_id}",
            reference_type="deal",
            reference_id=deal_id,
            currency="XTR",
            debit_account=("telegram_stars_clearing", charge_id),
            credit_account=("escrow", deal_id),
            debit_amount=intent["amount"],
            credit_amount=intent["amount"],
            now=timestamp,
        )
        append_escrow_event(
            conn,
            deal_id=deal_id,
            actor_user_id=user_id,
            event_type="escrow_funded",
            event_key=f"{deal_id}:stars-escrow-funded:{intent['id']}",
            event_data={
                "provider": "telegram_stars",
                "payment_intent_id": intent["id"],
                "ledger_transaction_id": ledger["id"],
                "currency": "XTR",
                "amount": str(intent["amount"]),
                "custody": "telegram_bot_stars_balance",
            },
            now=timestamp,
        )
        conn.commit()
    return {"deal_id": deal_id, "payment_intent_id": intent["id"], "status": "paid", "currency": "XTR"}
