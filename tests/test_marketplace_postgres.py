import os
import uuid

import pytest


def test_postgres_core_marketplace_schema_bootstrap_is_idempotent_and_verified():
    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")
    import psycopg
    from db_postgres_compat import PostgresConnectionAdapter
    from marketplace_postgres import initialize_postgres_marketplace_schema

    schema = "core_market_" + uuid.uuid4().hex[:10]
    raw = psycopg.connect(dsn, autocommit=True)
    adapter = PostgresConnectionAdapter(raw)
    try:
        adapter.execute(f'CREATE SCHEMA "{schema}"')
        adapter.execute(f'SET search_path TO "{schema}"')
        first = initialize_postgres_marketplace_schema(adapter)
        second = initialize_postgres_marketplace_schema(adapter)
        assert first["verified"] is True
        assert second["verified"] is True
        assert first["tables_verified"] == 16
        assert first["indexes_verified"] == 16
        assert first["triggers_verified"] == 1

        adapter.execute(
            "INSERT INTO telegram_users "
            "(telegram_id,created_at,updated_at) VALUES (?,1,1)",
            ("owner-1",),
        )
        adapter.execute(
            "INSERT INTO verified_assets "
            "(id,source,external_asset_id,owner_telegram_id,title,asset_type,"
            "verification_method,verification_reference_hash,created_at,verified_at) "
            "VALUES (?,?,?,?,?,'gift','telegram_gift_owner','hash-1',1,1)",
            ("asset-1", "telegram_gift", "gift-1", "owner-1", "Gift"),
        )
        # Ordinary metadata edits and no-op metadata updates are both blocked,
        # matching SQLite's BEFORE UPDATE OF ... WHEN NOT (ownership-transfer) guard.
        with pytest.raises(Exception, match="verified asset metadata is immutable"):
            adapter.execute(
                "UPDATE verified_assets SET title=? WHERE id=?",
                ("Changed", "asset-1"),
            )
        with pytest.raises(Exception, match="verified asset metadata is immutable"):
            adapter.execute(
                "UPDATE verified_assets SET title=title WHERE id=?",
                ("asset-1",),
            )

        # A verified ownership transfer is the one permitted metadata-column
        # update, provided source and external asset identity remain unchanged.
        adapter.execute(
            "UPDATE verified_assets SET owner_telegram_id=?, status='verified' WHERE id=?",
            ("owner-2", "asset-1"),
        )
        owner = adapter.execute(
            "SELECT owner_telegram_id FROM verified_assets WHERE id=?",
            ("asset-1",),
        ).fetchone()
        assert owner["owner_telegram_id"] == "owner-2"

        # The exception does not permit changing the asset's identity or
        # transferring an asset into a revoked state.
        with pytest.raises(Exception, match="verified asset metadata is immutable"):
            adapter.execute(
                "UPDATE verified_assets SET owner_telegram_id=?, external_asset_id=? WHERE id=?",
                ("owner-3", "different-gift", "asset-1"),
            )
        with pytest.raises(Exception, match="verified asset metadata is immutable"):
            adapter.execute(
                "UPDATE verified_assets SET owner_telegram_id=?, status='revoked' WHERE id=?",
                ("owner-3", "asset-1"),
            )
    finally:
        try:
            adapter.execute("SET search_path TO public")
            adapter.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            adapter.close()


def test_postgres_core_marketplace_bootstrap_rejects_non_postgres_connection():
    from marketplace_postgres import initialize_postgres_marketplace_schema

    class SQLiteLikeConnection:
        supports_for_update = False

        def execute(self, *_args, **_kwargs):
            raise AssertionError("must fail before executing SQL")

    with pytest.raises(TypeError, match="PostgreSQL adapter"):
        initialize_postgres_marketplace_schema(SQLiteLikeConnection())
