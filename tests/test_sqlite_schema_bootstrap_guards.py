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


def test_marketplace_schema_initializer_dispatches_to_postgres_bootstrap():
    connection = PostgresLikeConnection()
    with patch("marketplace._db", return_value=nullcontext(connection)), patch(
        "marketplace_postgres.initialize_postgres_marketplace_schema",
        return_value={"verified": True},
    ) as initialize:
        marketplace.init_marketplace_db()
    initialize.assert_called_once_with(connection)
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
            if "information_schema.tables" in statement:
                return self._rows("ton_proof_challenges", "verified_wallets")
            if "FROM pg_indexes" in statement:
                return self._rows("idx_ton_proof_challenges_expiry", "idx_verified_wallets_owner")
            if "information_schema.columns" in statement:
                columns = {
                    "ton_proof_challenges": (
                        "payload_hash", "telegram_id", "session_hash",
                        "created_at", "expires_at", "consumed_at",
                    ),
                    "verified_wallets": (
                        "network", "address", "telegram_id", "public_key",
                        "state_init_hash", "created_at", "last_verified_at", "revoked_at",
                    ),
                }
                return self._rows(*columns[parameters[0]])
            return None

        @staticmethod
        def _rows(*names):
            return type("Rows", (), {
                "fetchall": lambda self: [{"name": name} for name in names]
            })()

    connection = RecordingPostgresConnection()
    with patch("ton_connect._db", return_value=nullcontext(connection)):
        ton_connect.init_ton_connect_db()
    ddl = [statement for statement, _ in connection.statements]
    assert sum(statement.lstrip().startswith("CREATE TABLE IF NOT EXISTS") for statement in ddl) == 2
    assert sum(statement.lstrip().startswith("CREATE INDEX IF NOT EXISTS") for statement in ddl) == 2


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
