import sqlite3

import pytest

from marketplace_payments import (
    PaymentConflict,
    PaymentStateError,
    create_payment_intent,
    initialize_sqlite_payment_schema,
    post_ledger_transaction,
    record_payment_event,
    transition_payment_intent,
    verify_ledger_transaction,
)


def _connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE TABLE marketplace_deals (id TEXT PRIMARY KEY, status TEXT NOT NULL)")
    initialize_sqlite_payment_schema(conn)
    initialize_sqlite_payment_schema(conn)
    conn.execute("INSERT INTO marketplace_deals (id, status) VALUES ('deal-1', 'awaiting_payment')")
    conn.commit()
    return conn


def _create_stars_intent(conn, *, key="stars-order-1", amount="10"):
    return create_payment_intent(
        conn,
        deal_id="deal-1",
        provider="telegram_stars",
        currency="XTR",
        amount=amount,
        idempotency_key=key,
        expires_at=2000,
        metadata={"source": "checkout"},
        now=1000,
    )


def test_payment_intent_creation_is_idempotent_and_bound_to_ready_deal():
    conn = _connection()
    first = _create_stars_intent(conn)
    replay = _create_stars_intent(conn)
    assert first["id"] == replay["id"]
    assert first["status"] == "created"
    assert first["amount"] == "10"

    with pytest.raises(PaymentConflict):
        _create_stars_intent(conn, amount="11")
    with pytest.raises(sqlite3.IntegrityError):
        _create_stars_intent(conn, key="stars-order-duplicate")

    conn.execute("UPDATE marketplace_deals SET status='payment_not_enabled' WHERE id='deal-1'")
    with pytest.raises(PaymentStateError, match="awaiting payment"):
        create_payment_intent(
            conn,
            deal_id="deal-1",
            provider="ton",
            currency="TON",
            amount="1.25",
            idempotency_key="ton-order-2",
            expires_at=2000,
            now=1000,
        )
    conn.close()


def test_payment_intents_allow_only_stars_and_ton_with_provider_matched_currency():
    conn = _connection()
    with pytest.raises(ValueError, match="whole numbers"):
        create_payment_intent(
            conn, deal_id="deal-1", provider="telegram_stars", currency="XTR",
            amount="1.5", idempotency_key="stars-fraction", expires_at=2000, now=1000,
        )
    with pytest.raises(ValueError, match="do not match"):
        create_payment_intent(
            conn, deal_id="deal-1", provider="telegram_stars", currency="TON",
            amount="1", idempotency_key="stars-wrong-currency", expires_at=2000, now=1000,
        )
    with pytest.raises(ValueError, match="Unsupported payment currency"):
        create_payment_intent(
            conn, deal_id="deal-1", provider="ton", currency="USDT",
            amount="1", idempotency_key="usdt-disabled", expires_at=2000, now=1000,
        )
    intent = create_payment_intent(
        conn, deal_id="deal-1", provider="ton", currency="TON",
        amount="1.000000001", idempotency_key="ton-valid", expires_at=2000, now=1000,
    )
    assert intent["currency"] == "TON"
    assert intent["amount"] == "1.000000001"
    conn.close()


def test_payment_intent_state_machine_requires_provider_reference_to_confirm():
    conn = _connection()
    intent = _create_stars_intent(conn)
    pending = transition_payment_intent(conn, intent["id"], "pending", now=1100)
    assert pending["status"] == "pending"
    with pytest.raises(PaymentStateError, match="provider transaction reference"):
        transition_payment_intent(conn, intent["id"], "confirmed", now=1200)
    confirmed = transition_payment_intent(
        conn, intent["id"], "confirmed", provider_reference="telegram-charge-1", now=1200
    )
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmed_at"] == 1200
    replay = transition_payment_intent(
        conn, intent["id"], "confirmed", provider_reference="telegram-charge-1", now=1300
    )
    assert replay["confirmed_at"] == 1200
    with pytest.raises(PaymentStateError, match="Invalid payment intent transition"):
        transition_payment_intent(conn, intent["id"], "pending", now=1400)
    conn.close()


def test_provider_events_are_idempotent_and_immutable():
    conn = _connection()
    intent = _create_stars_intent(conn)
    event = {
        "provider": "telegram_stars",
        "provider_event_id": "charge-1",
        "payment_intent_id": intent["id"],
        "event_type": "successful_payment",
        "payload_hash": "a" * 64,
        "now": 1100,
    }
    assert record_payment_event(conn, **event) is True
    assert record_payment_event(conn, **event) is False
    conflict = {**event, "payload_hash": "b" * 64}
    with pytest.raises(PaymentConflict):
        record_payment_event(conn, **conflict)
    with pytest.raises(sqlite3.IntegrityError, match="payment events are immutable"):
        conn.execute("UPDATE marketplace_payment_events SET event_type='forged' WHERE provider_event_id='charge-1'")
    with pytest.raises(sqlite3.IntegrityError, match="payment events are immutable"):
        conn.execute("DELETE FROM marketplace_payment_events WHERE provider_event_id='charge-1'")
    conn.close()


def test_double_entry_ledger_posts_balanced_pair_once_and_is_immutable():
    conn = _connection()
    posted = post_ledger_transaction(
        conn,
        idempotency_key="ledger-payment-1",
        reference_type="deal",
        reference_id="deal-1",
        currency="TON",
        debit_account=("buyer", "101"),
        credit_account=("escrow", "deal-1"),
        debit_amount="2.5",
        credit_amount="2.500",
        now=1200,
    )
    assert posted["entries"] == 2
    assert posted["idempotent_replay"] is False
    assert verify_ledger_transaction(conn, posted["id"]) == {
        "transaction_id": posted["id"], "balanced": True, "entry_count": 2
    }

    replay = post_ledger_transaction(
        conn,
        idempotency_key="ledger-payment-1",
        reference_type="deal",
        reference_id="deal-1",
        currency="TON",
        debit_account=("buyer", "101"),
        credit_account=("escrow", "deal-1"),
        debit_amount="2.500",
        credit_amount="2.5",
        now=1300,
    )
    assert replay == {"id": posted["id"], "idempotent_replay": True, "entries": 2}

    with pytest.raises(PaymentConflict):
        post_ledger_transaction(
            conn,
            idempotency_key="ledger-payment-1",
            reference_type="deal",
            reference_id="deal-1",
            currency="TON",
            debit_account=("buyer", "101"),
            credit_account=("seller", "202"),
            debit_amount="2.5",
            credit_amount="2.5",
            now=1300,
        )
    with pytest.raises(ValueError, match="must balance"):
        post_ledger_transaction(
            conn,
            idempotency_key="ledger-unbalanced",
            reference_type="deal",
            reference_id="deal-1",
            currency="TON",
            debit_account=("buyer", "101"),
            credit_account=("escrow", "deal-1"),
            debit_amount="2.5",
            credit_amount="2.4",
            now=1300,
        )
    with pytest.raises(sqlite3.IntegrityError, match="ledger entries are immutable"):
        conn.execute("UPDATE marketplace_ledger_entries SET amount='99' WHERE transaction_id=?", (posted["id"],))
    with pytest.raises(sqlite3.IntegrityError, match="ledger transactions are immutable"):
        conn.execute("DELETE FROM marketplace_ledger_transactions WHERE id=?", (posted["id"],))
    conn.close()


def test_ledger_posting_rolls_back_both_entries_if_second_insert_fails():
    conn = _connection()
    conn.execute("""
        CREATE TRIGGER fail_ledger_credit
        BEFORE INSERT ON marketplace_ledger_entries
        WHEN NEW.direction = 'credit'
        BEGIN
            SELECT RAISE(ABORT, 'simulated credit failure');
        END
    """)
    with pytest.raises(sqlite3.IntegrityError, match="simulated credit failure"):
        post_ledger_transaction(
            conn,
            idempotency_key="ledger-atomic-1",
            reference_type="deal",
            reference_id="deal-1",
            currency="XTR",
            debit_account=("buyer", "101"),
            credit_account=("escrow", "deal-1"),
            debit_amount="10",
            credit_amount="10",
            now=1200,
        )
    assert conn.execute("SELECT COUNT(*) FROM marketplace_ledger_transactions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM marketplace_ledger_entries").fetchone()[0] == 0
    conn.close()
