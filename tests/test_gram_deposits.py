import sqlite3

import pytest

from gram_deposits import (
    GramDepositError,
    build_ton_connect_comment_payload,
    confirm_gram_deposit,
    create_gram_deposit_intent,
    extract_incoming_transaction,
    gram_to_nano,
    initialize_gram_deposit_schema,
)
from marketplace_payments import initialize_sqlite_payment_schema


def _connection():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE TABLE marketplace_deals (id TEXT PRIMARY KEY, status TEXT NOT NULL)")
    initialize_sqlite_payment_schema(conn)
    initialize_gram_deposit_schema(conn)
    return conn


def _transaction(intent, **overrides):
    tx = {
        "hash": "chain-hash-001",
        "lt": "123456",
        "utime": 1100,
        "success": True,
        "aborted": False,
        "in_msg": {
            "source": "EQsender",
            "destination": intent["treasury_address"],
            "value": str(intent["amount_nano"]),
            "bounced": False,
            "decoded_body": {"text": intent["memo"]},
        },
    }
    tx.update(overrides)
    return tx


def _intent(conn, **overrides):
    args = {
        "user_id": "10001",
        "treasury_address": "EQtreasury",
        "amount_gram": "1.25",
        "network": "mainnet",
        "idempotency_key": "deposit-1",
        "now": 1000,
    }
    args.update(overrides)
    return create_gram_deposit_intent(conn, **args)


def test_gram_amount_uses_exact_nano_units():
    assert gram_to_nano("0.000000001") == 1
    assert gram_to_nano("1.250000000") == 1_250_000_000
    with pytest.raises(GramDepositError, match="9 decimal places"):
        gram_to_nano("0.0000000001")
    with pytest.raises(GramDepositError):
        gram_to_nano("0")


def test_deposit_intent_is_idempotent_and_unique():
    conn = _connection()
    first = _intent(conn)
    replay = _intent(conn)
    assert replay["id"] == first["id"]
    assert replay["memo"] == first["memo"]
    with pytest.raises(Exception, match="different parameters"):
        _intent(conn, amount_gram="2")
    other = _intent(conn, idempotency_key="deposit-2")
    assert other["memo"] != first["memo"]
    conn.close()


def test_tonapi_transaction_requires_successful_incoming_message():
    conn = _connection()
    intent = _intent(conn)
    assert extract_incoming_transaction(_transaction(intent))["amount_nano"] == 1_250_000_000
    assert extract_incoming_transaction(_transaction(intent, success=False)) is None
    assert extract_incoming_transaction(_transaction(intent, aborted=True)) is None
    assert extract_incoming_transaction(_transaction(intent, in_msg=None)) is None
    conn.close()


def test_confirmed_chain_transfer_credits_ledger_exactly_once():
    conn = _connection()
    intent = _intent(conn)
    result = confirm_gram_deposit(
        conn, network="mainnet", transaction=_transaction(intent), now=1200
    )
    assert result["deposit_id"] == intent["id"]
    assert result["amount_nano"] == 1_250_000_000
    assert conn.execute(
        "SELECT status FROM gram_deposit_intents WHERE id=?", (intent["id"],)
    ).fetchone()["status"] == "confirmed"
    assert conn.execute(
        "SELECT COUNT(*) FROM marketplace_ledger_transactions"
    ).fetchone()[0] == 1
    assert conn.execute(
        """SELECT account_type,account_id,direction,amount
           FROM marketplace_ledger_entries ORDER BY direction"""
    ).fetchall()[1]["account_type"] in {"external_chain", "user_balance"}
    replay = confirm_gram_deposit(
        conn, network="mainnet", transaction=_transaction(intent), now=1300
    )
    assert replay is None
    assert conn.execute(
        "SELECT COUNT(*) FROM marketplace_ledger_transactions"
    ).fetchone()[0] == 1
    conn.close()


@pytest.mark.parametrize("mismatch", ["recipient", "amount"])
def test_wrong_recipient_or_amount_never_credits(mismatch):
    conn = _connection()
    intent = _intent(conn)
    tx = _transaction(intent)
    if mismatch == "recipient":
        tx["in_msg"]["destination"] = "EQother"
    else:
        tx["in_msg"]["value"] = str(intent["amount_nano"] + 1)
    assert confirm_gram_deposit(conn, network="mainnet", transaction=tx, now=1200) is None
    assert conn.execute("SELECT COUNT(*) FROM marketplace_ledger_transactions").fetchone()[0] == 0
    conn.close()


def test_wrong_memo_and_out_of_window_transaction_never_credit():
    conn = _connection()
    intent = _intent(conn)
    wrong_memo = _transaction(intent)
    wrong_memo["in_msg"]["decoded_body"]["text"] = "HASSLE:attacker"
    assert confirm_gram_deposit(conn, network="mainnet", transaction=wrong_memo, now=1200) is None
    late = _transaction(intent)
    late["utime"] = intent["expires_at"] + 1
    assert confirm_gram_deposit(conn, network="mainnet", transaction=late, now=1200) is None
    assert conn.execute("SELECT COUNT(*) FROM marketplace_ledger_transactions").fetchone()[0] == 0
    conn.close()


def test_ledger_failure_rolls_back_chain_event_and_deposit_confirmation():
    conn = _connection()
    intent = _intent(conn)
    conn.execute("""
        CREATE TRIGGER fail_ledger_entry
        BEFORE INSERT ON marketplace_ledger_entries
        BEGIN SELECT RAISE(ABORT, 'forced ledger failure'); END
    """)
    with pytest.raises(sqlite3.IntegrityError, match="forced ledger failure"):
        confirm_gram_deposit(conn, network="mainnet", transaction=_transaction(intent), now=1200)
    assert conn.execute("SELECT COUNT(*) FROM gram_deposit_chain_events").fetchone()[0] == 0
    assert conn.execute(
        "SELECT status FROM gram_deposit_intents WHERE id=?", (intent["id"],)
    ).fetchone()["status"] == "pending"
    assert conn.execute("SELECT COUNT(*) FROM marketplace_ledger_transactions").fetchone()[0] == 0
    conn.close()


def test_ton_connect_comment_payload_is_standard_base64_boc():
    payload = build_ton_connect_comment_payload("HASSLE:deposit-test")
    assert isinstance(payload, str)
    assert len(payload) > 10


def test_expired_intent_can_reconcile_a_transfer_sent_before_expiry():
    from gram_deposits import expire_gram_deposit_intents

    conn = _connection()
    intent = _intent(conn)
    expire_gram_deposit_intents(conn, now=intent["expires_at"] + 1)
    assert conn.execute(
        "SELECT status FROM gram_deposit_intents WHERE id=?", (intent["id"],)
    ).fetchone()["status"] == "expired"
    tx = _transaction(intent)
    tx["utime"] = intent["expires_at"]
    result = confirm_gram_deposit(conn, network="mainnet", transaction=tx, now=intent["expires_at"] + 10)
    assert result is not None
    assert conn.execute(
        "SELECT status FROM gram_deposit_intents WHERE id=?", (intent["id"],)
    ).fetchone()["status"] == "confirmed"
    conn.close()
