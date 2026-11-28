from contextlib import contextmanager
import sqlite3

import pytest

import gram_payments as gp


RAW_TREASURY = "0:" + "1" * 64


def _db_connection():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _patch_db(monkeypatch, connection):
    @contextmanager
    def manager():
        yield connection
    monkeypatch.setattr(gp, "_db", manager)


def _event(memo, *, recipient=RAW_TREASURY, amount="2500000000", in_progress=False, is_scam=False):
    return {
        "event_id": "event-hash-001",
        "lt": 123456,
        "in_progress": in_progress,
        "is_scam": is_scam,
        "actions": [],
    }


def _transfer(memo, *, recipient=RAW_TREASURY, amount="2500000000"):
    return {
        "sender": {"address": "0:" + "2" * 64},
        "recipient": {"address": recipient},
        "amount": amount,
        "comment": memo,
    }


def _prepare(monkeypatch):
    connection = _db_connection()
    _patch_db(monkeypatch, connection)
    monkeypatch.setenv("HASSLE_GRAM_DEPOSITS_ENABLED", "1")
    monkeypatch.setenv("HASSLE_TON_NETWORK", "mainnet")
    monkeypatch.setenv("HASSLE_TON_TREASURY_ADDRESS", RAW_TREASURY)
    monkeypatch.setenv("TONAPI_TOKEN", "test-token")
    connection.execute("CREATE TABLE marketplace_deals (id TEXT PRIMARY KEY, status TEXT NOT NULL)")
    from marketplace_payments import initialize_sqlite_payment_schema
    initialize_sqlite_payment_schema(connection)
    gp.init_gram_deposit_schema()
    return connection


def _deposit(monkeypatch, connection):
    # The payload is a standard TON text-comment cell, produced by tonsdk.
    return gp.create_gram_deposit("123456", gp.Decimal("2.5"), now=1000)


def test_amounts_are_converted_to_nano_without_float_rounding():
    assert gp._amount_to_nano(gp.Decimal("1.000000001")) == 1_000_000_001
    assert gp._nano_to_gram_text(1_000_000_001) == "1.000000001"
    with pytest.raises(ValueError):
        gp._amount_to_nano(gp.Decimal("0.0000000001"))


def test_deposit_intent_is_unique_and_returns_ton_connect_transaction(monkeypatch):
    connection = _prepare(monkeypatch)
    deposit = _deposit(monkeypatch, connection)
    assert deposit["currency"] == "GRAM"
    assert deposit["amount_nano"] == "2500000000"
    assert deposit["address"] == RAW_TREASURY
    assert deposit["memo"].startswith("HASSLE-GRAM-")
    assert deposit["payload"]
    assert deposit["status"] == "pending"
    stored = gp.get_gram_deposit("123456", deposit["id"])
    assert stored["status"] == "pending"
    assert gp.get_gram_deposit("999999", deposit["id"]) is None
    connection.close()


def test_indexed_exact_transfer_credits_once_and_records_double_entry(monkeypatch):
    connection = _prepare(monkeypatch)
    deposit = _deposit(monkeypatch, connection)
    event = {"event_id": "event-hash-001", "lt": 123456,
             "in_progress": False, "is_scam": False}
    transfer = _transfer(deposit["memo"])
    assert gp.confirm_gram_deposit_event(
        network="mainnet", event=event, action_index=0, transfer=transfer, now=1100
    ) is True
    assert gp.confirm_gram_deposit_event(
        network="mainnet", event=event, action_index=0, transfer=transfer, now=1101
    ) is False
    balance = connection.execute(
        "SELECT available_nano FROM gram_wallet_balances WHERE telegram_id='123456'"
    ).fetchone()
    assert balance["available_nano"] == "2500000000"
    assert connection.execute("SELECT COUNT(*) FROM gram_ledger_transactions").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM gram_ledger_entries").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM gram_chain_events").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM marketplace_ledger_transactions WHERE idempotency_key=?", (f"gram-deposit:{deposit['id']}",)).fetchone()[0] == 1
    canonical_balance = connection.execute("SELECT SUM(CASE WHEN direction='credit' THEN CAST(amount AS NUMERIC) ELSE -CAST(amount AS NUMERIC) END) FROM marketplace_ledger_entries WHERE account_type='user_balance' AND account_id='123456' AND currency='TON'").fetchone()[0]
    assert str(canonical_balance) == "2.5"
    assert connection.execute(
        "SELECT status FROM gram_deposit_intents WHERE id=?", (deposit["id"],)
    ).fetchone()["status"] == "confirmed"
    connection.close()


@pytest.mark.parametrize("event,transfer", [
    ({"event_id": "e1", "in_progress": True, "is_scam": False},
     _transfer("memo")),
    ({"event_id": "e2", "in_progress": False, "is_scam": True},
     _transfer("memo")),
])
def test_unfinalized_or_scam_events_are_not_credited(monkeypatch, event, transfer):
    connection = _prepare(monkeypatch)
    deposit = _deposit(monkeypatch, connection)
    transfer["comment"] = deposit["memo"]
    assert gp.confirm_gram_deposit_event(
        network="mainnet", event=event, action_index=0, transfer=transfer, now=1100
    ) is False
    assert connection.execute("SELECT COUNT(*) FROM gram_wallet_balances").fetchone()[0] == 0
    connection.close()


def test_wrong_recipient_or_amount_or_memo_never_credits(monkeypatch):
    connection = _prepare(monkeypatch)
    deposit = _deposit(monkeypatch, connection)
    event = {"event_id": "event-hash-001", "in_progress": False, "is_scam": False}
    for transfer in (
        _transfer(deposit["memo"], recipient="0:" + "3" * 64),
        _transfer(deposit["memo"], amount="2499999999"),
        _transfer("HASSLE-GRAM-wrong-memo"),
    ):
        assert gp.confirm_gram_deposit_event(
            network="mainnet", event=event, action_index=0, transfer=transfer, now=1100
        ) is False
    assert connection.execute("SELECT COUNT(*) FROM gram_wallet_balances").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM gram_chain_events").fetchone()[0] == 0
    connection.close()


def test_late_onchain_transfer_is_still_credited_after_ui_expiry(monkeypatch):
    connection = _prepare(monkeypatch)
    deposit = _deposit(monkeypatch, connection)
    event = {"event_id": "event-hash-late", "in_progress": False, "is_scam": False}
    assert gp.confirm_gram_deposit_event(
        network="mainnet", event=event, action_index=0,
        transfer=_transfer(deposit["memo"]), now=deposit["expires_at"] + 1
    ) is True
    assert connection.execute(
        "SELECT available_nano FROM gram_wallet_balances WHERE telegram_id='123456'"
    ).fetchone()["available_nano"] == "2500000000"
    connection.close()


def test_deposits_fail_closed_without_explicit_feature_flag(monkeypatch):
    monkeypatch.setenv("HASSLE_GRAM_DEPOSITS_ENABLED", "0")
    with pytest.raises(Exception) as error:
        gp.create_gram_deposit("123456", gp.Decimal("1"))
    assert getattr(error.value, "status_code", None) == 503
