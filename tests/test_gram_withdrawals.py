import sqlite3

import pytest

from gram_withdrawals import (
    GramWithdrawalError,
    create_gram_withdrawal,
    initialize_gram_withdrawal_schema,
    list_gram_withdrawals,
    transition_gram_withdrawal,
)
from marketplace_payments import initialize_sqlite_payment_schema, post_ledger_transaction


DESTINATION = "0:" + "11" * 32


def _connection():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE TABLE marketplace_deals (id TEXT PRIMARY KEY, status TEXT NOT NULL)")
    initialize_sqlite_payment_schema(conn)
    initialize_gram_withdrawal_schema(conn)
    post_ledger_transaction(
        conn, idempotency_key="fund-user-1", reference_type="payment_intent",
        reference_id="deposit-1", currency="TON",
        debit_account=("external_chain", "mainnet:treasury"),
        credit_account=("user_balance", "10001"),
        debit_amount="10", credit_amount="10", now=1000,
    )
    return conn


def _withdraw(conn, **overrides):
    args = {
        "user_id": "10001",
        "destination_address": DESTINATION,
        "amount_gram": "3",
        "network": "mainnet",
        "idempotency_key": "withdraw-1",
        "fee_bps": 100,
        "now": 1100,
    }
    args.update(overrides)
    return create_gram_withdrawal(conn, **args)


def _balance(conn, account_type, account_id):
    rows = conn.execute(
        """SELECT direction,amount FROM marketplace_ledger_entries
           WHERE account_type=? AND account_id=? AND currency='TON'""",
        (account_type, account_id),
    ).fetchall()
    from decimal import Decimal
    return sum((Decimal(r["amount"]) if r["direction"] == "credit" else -Decimal(r["amount"]) for r in rows), Decimal("0"))


def test_withdrawal_reserves_gross_amount_and_calculates_fee_in_nano_units():
    conn = _connection()
    withdrawal = _withdraw(conn)
    assert withdrawal["status"] == "queued"
    assert withdrawal["amount_nano"] == 3_000_000_000
    assert withdrawal["fee_nano"] == 30_000_000
    assert withdrawal["payout_nano"] == 2_970_000_000
    assert _balance(conn, "user_balance", "10001") == 7
    assert _balance(conn, "user_withdrawal_hold", withdrawal["id"]) == 2.97
    assert _balance(conn, "platform_fee_pending", withdrawal["id"]) == 0.03
    conn.close()


def test_withdrawal_idempotency_is_scoped_to_user_and_parameters():
    conn = _connection()
    first = _withdraw(conn)
    replay = _withdraw(conn)
    assert first["id"] == replay["id"]
    with pytest.raises(Exception, match="different parameters"):
        _withdraw(conn, amount_gram="4")
    assert conn.execute("SELECT COUNT(*) FROM gram_withdrawals").fetchone()[0] == 1
    assert _balance(conn, "user_balance", "10001") == 7
    conn.close()


def test_insufficient_balance_rolls_back_withdrawal_and_reserve():
    conn = _connection()
    with pytest.raises(GramWithdrawalError, match="Insufficient"):
        _withdraw(conn, amount_gram="11")
    assert conn.execute("SELECT COUNT(*) FROM gram_withdrawals").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM marketplace_ledger_transactions").fetchone()[0] == 1
    assert _balance(conn, "user_balance", "10001") == 10
    conn.close()


def test_failed_withdrawal_releases_net_and_fee_back_to_user():
    conn = _connection()
    withdrawal = _withdraw(conn)
    failed = transition_gram_withdrawal(
        conn, withdrawal_id=withdrawal["id"], new_status="failed",
        event_key="withdrawal-1:failed", failure_code="signer_unavailable", now=1200,
    )
    assert failed["status"] == "failed"
    assert _balance(conn, "user_balance", "10001") == 10
    assert _balance(conn, "user_withdrawal_hold", withdrawal["id"]) == 0
    assert _balance(conn, "platform_fee_pending", withdrawal["id"]) == 0
    conn.close()


def test_confirmed_withdrawal_debits_reserved_net_only_after_chain_reference():
    conn = _connection()
    withdrawal = _withdraw(conn)
    transition_gram_withdrawal(
        conn, withdrawal_id=withdrawal["id"], new_status="awaiting_multisig",
        event_key="withdrawal-1:awaiting", now=1150,
    )
    submitted = transition_gram_withdrawal(
        conn, withdrawal_id=withdrawal["id"], new_status="submitted",
        event_key="withdrawal-1:submitted", chain_tx_hash="chain-hash",
        chain_lt="123456", now=1200,
    )
    assert submitted["status"] == "submitted"
    confirmed = transition_gram_withdrawal(
        conn, withdrawal_id=withdrawal["id"], new_status="confirmed",
        event_key="withdrawal-1:confirmed", now=1300,
    )
    assert confirmed["status"] == "confirmed"
    assert confirmed["chain_tx_hash"] == "chain-hash"
    assert _balance(conn, "user_balance", "10001") == 7
    assert _balance(conn, "user_withdrawal_hold", withdrawal["id"]) == 0
    assert _balance(conn, "platform_fee_pending", withdrawal["id"]) == 0.03
    assert _balance(conn, "external_chain", "mainnet:" + DESTINATION) == 2.97
    conn.close()


def test_submitted_and_confirmed_states_require_chain_reference_and_valid_order():
    conn = _connection()
    withdrawal = _withdraw(conn)
    with pytest.raises(Exception, match="requires an on-chain transaction reference"):
        transition_gram_withdrawal(
            conn, withdrawal_id=withdrawal["id"], new_status="submitted",
            event_key="withdrawal-1:no-tx", now=1200,
        )
    with pytest.raises(Exception, match="Invalid withdrawal transition"):
        transition_gram_withdrawal(
            conn, withdrawal_id=withdrawal["id"], new_status="confirmed",
            event_key="withdrawal-1:skip-to-confirmed", now=1200,
        )
    assert list_gram_withdrawals(conn, "10001")[0]["id"] == withdrawal["id"]
    conn.close()


def test_withdrawal_fee_is_bounded_and_destination_is_validated():
    conn = _connection()
    with pytest.raises(GramWithdrawalError, match="fee"):
        _withdraw(conn, fee_bps=201)
    with pytest.raises(GramWithdrawalError, match="destination"):
        _withdraw(conn, destination_address="not-a-wallet")
    conn.close()
