from contextlib import nullcontext
import sqlite3
from unittest.mock import patch

import marketplace
import marketplace_offers
import ton_connect
from database import require_sqlite_connection


class PostgresLikeConnection:
    supports_for_update = True

    def __init__(self):
        self.statements = []

    def execute(self, statement, parameters=()):
        self.statements.append((statement, parameters))
        raise AssertionError("SQLite bootstrap must reject this connection before SQL")


def test_sqlite_guard_accepts_real_sqlite_connection():
    connection = sqlite3.connect(":memory:")
    try:
        assert require_sqlite_connection(connection, "test") is connection
    finally:
        connection.close()


def test_sqlite_guard_rejects_postgres_adapter_before_sql():
    connection = PostgresLikeConnection()
    try:
        require_sqlite_connection(connection, "test schema initialization")
    except RuntimeError as error:
        assert "requires SQLite" in str(error)
        assert "reviewed migration path" in str(error)
    else:
        raise AssertionError("PostgreSQL-like connection must be rejected")
    assert connection.statements == []


def test_marketplace_schema_initializer_fails_before_legacy_migrations():
    connection = PostgresLikeConnection()
    with patch("marketplace._db", return_value=nullcontext(connection)):
        try:
            marketplace.init_marketplace_db()
        except RuntimeError as error:
            assert "Marketplace schema initialization requires SQLite" in str(error)
        else:
            raise AssertionError("SQLite-only marketplace bootstrap must fail closed")
    assert connection.statements == []


def test_marketplace_offers_schema_initializer_dispatches_to_postgres_bootstrap():
    connection = PostgresLikeConnection()
    with patch("marketplace_offers._db", return_value=nullcontext(connection)), patch(
        "marketplace_offers_postgres.initialize_postgres_offer_schema",
        return_value={"verified": True},
    ) as initialize:
        marketplace_offers.init_marketplace_offers_db()
    initialize.assert_called_once_with(connection)
    assert connection.statements == []


def test_ton_connect_schema_initializer_uses_portable_single_statement_ddl():
    class RecordingPostgresConnection:
        supports_for_update = True

        def __init__(self):
            self.statements = []

        def execute(self, statement, parameters=()):
            self.statements.append((statement, parameters))

    connection = RecordingPostgresConnection()
    with patch("ton_connect._db", return_value=nullcontext(connection)):
        ton_connect.init_ton_connect_db()
    assert len(connection.statements) == 4
    assert connection.statements[0][0].lstrip().startswith("CREATE TABLE IF NOT EXISTS ton_proof_challenges")
    assert connection.statements[2][0].lstrip().startswith("CREATE TABLE IF NOT EXISTS verified_wallets")


def test_legacy_marketplace_migrations_reject_postgres_adapter_before_catalog_queries():
    connection = PostgresLikeConnection()
    for migration in (
        marketplace._migrate_legacy_marketplace_table_names,
        marketplace._migrate_legacy_order_event_types,
    ):
        try:
            migration(connection)
        except RuntimeError as error:
            assert "requires SQLite" in str(error)
        else:
            raise AssertionError("SQLite-only legacy migration must fail closed")
    assert connection.statements == []
