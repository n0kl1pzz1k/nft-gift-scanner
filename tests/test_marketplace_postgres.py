import os
import time
import uuid

import pytest


def test_postgres_core_marketplace_schema_bootstrap_is_idempotent_and_verified():
    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")
    import psycopg
    from db_postgres_compat import PostgresConnectionAdapter
    from marketplace_postgres import initialize_postgres_marketplace_schema, verify_postgres_marketplace_schema

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
        assert first["columns_verified"] > 0
        assert second["columns_verified"] == first["columns_verified"]
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

        # The verifier must catch stale pre-existing tables even when every
        # expected table, index, and trigger name is present.
        adapter.execute("ALTER TABLE user_profiles DROP COLUMN bio")
        with pytest.raises(RuntimeError, match="missing columns in user_profiles: bio"):
            verify_postgres_marketplace_schema(adapter)
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



def test_postgres_runtime_smoke_initializes_auth_assets_listing_and_readiness():
    """Exercise real marketplace request paths through the opt-in PostgreSQL factory."""
    import asyncio
    from contextlib import contextmanager
    from unittest.mock import patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    import psycopg
    import database
    import marketplace
    import marketplace_offers
    import ton_connect
    from marketplace import create_marketplace_router
    from marketplace_offers import create_marketplace_offers_router
    from ton_connect import init_ton_connect_db

    schema = "runtime_smoke_" + uuid.uuid4().hex[:10]
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')

    @contextmanager
    def scoped_connection():
        connection = database.connect()
        try:
            yield connection
        finally:
            connection.close()

    try:
        with patch.dict(os.environ, {
            "HASSLE_NFT_DB_BACKEND": "postgres",
            "DATABASE_URL": dsn,
            "HASSLE_POSTGRES_SCHEMA": schema,
            "HASSLE_VERIFIED_ASSETS_ENABLED": "true",
        }, clear=False), patch.object(marketplace, "_db", side_effect=scoped_connection), patch.object(
            marketplace_offers, "_db", side_effect=scoped_connection
        ), patch.object(ton_connect, "_db", side_effect=scoped_connection):
            marketplace.init_marketplace_db()
            marketplace_offers.init_marketplace_offers_db()
            init_ton_connect_db()

            user = marketplace.upsert_telegram_user({
                "id": "123456789", "username": "postgres_smoke",
                "first_name": "Postgres", "language_code": "en",
            })
            assert user["id"] == "123456789"

            token = marketplace.create_telegram_session("123456789")
            session_user = marketplace.get_telegram_session_user(token)
            assert session_user and session_user["id"] == "123456789"

            init_data = "hash=" + ("a" * 64)
            assert marketplace.consume_telegram_init_data(init_data, "123456789") is True
            assert marketplace.consume_telegram_init_data(init_data, "123456789") is False

            asset = marketplace.register_verified_asset(
                source="ton_nft",
                external_asset_id="ton-nft-postgres-smoke",
                owner_telegram_id="123456789",
                title="Verified PostgreSQL Gift",
                asset_type="gift",
                image_url="",
                verification_method="ton_connect_proof",
                verification_reference="verified-reference-postgres-smoke",
            )
            assert asset["owner_telegram_id"] == "123456789"

            api = FastAPI()
            verify_user = lambda _value: {
                "id": "123456789", "first_name": "Postgres",
                "username": "postgres_smoke",
            }
            api.include_router(create_marketplace_router(verify_user))
            api.include_router(create_marketplace_offers_router(verify_user))
            with patch.object(marketplace, "_verified_asset_actions_enabled", return_value=True), patch.object(
                marketplace, "_test_ton_owner_recheck_skipped", return_value=True
            ):
                with TestClient(api) as client:
                    response = client.post(
                        "/api/marketplace/orders",
                        headers={"X-Telegram-Init-Data": "verified-test-credential"},
                        json={
                            "kind": "listing",
                            "asset_id": asset["id"],
                            "title": "Client title must not override verified metadata",
                            "price": "12.5",
                            "currency": "TON",
                            "note": "PostgreSQL runtime smoke test",
                        },
                    )
            assert response.status_code == 201, response.text
            assert response.json()["item"]["title"] == "Verified PostgreSQL Gift"

            app_module = __import__("app")
            readiness = asyncio.run(app_module.readiness())
            assert readiness["status"] == "ready"

    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute("SET search_path TO public")
            admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')



def test_postgres_ownership_transfer_rolls_back_then_cancels_stale_listing():
    """Exercise transfer atomicity against real PostgreSQL, including dependent offers."""
    from contextlib import contextmanager
    from unittest.mock import patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    import psycopg
    import database
    import marketplace
    import marketplace_offers
    from marketplace import create_marketplace_router
    from marketplace_offers import create_marketplace_offers_router

    schema = "transfer_atomic_" + uuid.uuid4().hex[:10]
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')

    @contextmanager
    def scoped_connection():
        connection = database.connect()
        try:
            yield connection
        finally:
            connection.close()

    try:
        with patch.dict(os.environ, {
            "HASSLE_NFT_DB_BACKEND": "postgres",
            "DATABASE_URL": dsn,
            "HASSLE_POSTGRES_SCHEMA": schema,
            "HASSLE_VERIFIED_ASSETS_ENABLED": "true",
        }, clear=False), patch.object(
            marketplace, "_db", side_effect=scoped_connection
        ), patch.object(
            marketplace_offers, "_db", side_effect=scoped_connection
        ):
            marketplace.init_marketplace_db()
            marketplace_offers.init_marketplace_offers_db()
            marketplace.upsert_telegram_user({
                "id": "101", "username": "seller", "first_name": "Seller",
            })
            marketplace.upsert_telegram_user({
                "id": "202", "username": "buyer", "first_name": "Buyer",
            })

            asset = marketplace.register_verified_asset(
                source="ton_nft",
                external_asset_id="ton-nft-transfer-atomicity",
                owner_telegram_id="101",
                title="Original verified gift",
                asset_type="gift",
                image_url="",
                verification_method="ton_connect_proof",
                verification_reference="original-transfer-verification-reference",
            )

            api = FastAPI()
            def verify_user(value):
                user_id = {"seller": "101", "buyer": "202", "outsider": "303"}[value]
                names = {"101": ("Seller", "seller"), "202": ("Buyer", "buyer"),
                         "303": ("Outsider", "outsider")}
                first_name, username = names[user_id]
                return {"id": user_id, "first_name": first_name, "username": username}
            api.include_router(create_marketplace_router(verify_user))
            api.include_router(create_marketplace_offers_router(verify_user))

            with patch.object(
                marketplace, "_verified_asset_actions_enabled", return_value=True
            ), patch.object(
                marketplace, "_test_ton_owner_recheck_skipped", return_value=True
            ), TestClient(api) as client:
                listing_response = client.post(
                    "/api/marketplace/orders",
                    headers={"X-Telegram-Init-Data": "seller"},
                    json={
                        "kind": "listing",
                        "asset_id": asset["id"],
                        "title": "Client title is ignored",
                        "price": "3",
                        "currency": "TON",
                    },
                )
                assert listing_response.status_code == 201, listing_response.text
                order_id = listing_response.json()["item"]["id"]

                offer_response = client.post(
                    "/api/marketplace/offers",
                    headers={"X-Telegram-Init-Data": "buyer"},
                    json={"order_id": order_id, "amount": "2"},
                )
                assert offer_response.status_code == 201, offer_response.text
                offer_id = offer_response.json()["item"]["id"]

                with database.db() as conn:
                    conn.execute("""
                        CREATE FUNCTION test_fail_ownership_history()
                        RETURNS trigger LANGUAGE plpgsql AS $$
                        BEGIN
                            RAISE EXCEPTION 'injected ownership-history failure';
                        END;
                        $$
                    """)
                    conn.execute("""
                        CREATE TRIGGER test_fail_ownership_history
                        BEFORE INSERT ON asset_ownership_history
                        FOR EACH ROW EXECUTE FUNCTION test_fail_ownership_history()
                    """)

                with pytest.raises(psycopg.Error, match="injected ownership-history failure"):
                    marketplace.register_verified_asset(
                        source="ton_nft",
                        external_asset_id="ton-nft-transfer-atomicity",
                        owner_telegram_id="202",
                        title="New verified gift owner",
                        asset_type="gift",
                        image_url="",
                        verification_method="ton_connect_proof",
                        verification_reference="new-owner-transfer-verification-reference",
                        allow_owner_transfer=True,
                    )

                with database.db() as conn:
                    stored_asset = conn.execute(
                        "SELECT owner_telegram_id, title, status FROM verified_assets WHERE id=?",
                        (asset["id"],),
                    ).fetchone()
                    stored_listing = conn.execute(
                        "SELECT status FROM marketplace_orders WHERE id=?", (order_id,)
                    ).fetchone()
                    stored_offer = conn.execute(
                        "SELECT status FROM marketplace_offers WHERE id=?", (offer_id,)
                    ).fetchone()
                    history_count = conn.execute(
                        "SELECT COUNT(*) FROM asset_ownership_history WHERE asset_id=?",
                        (asset["id"],),
                    ).fetchone()[0]

                assert stored_asset["owner_telegram_id"] == "101"
                assert stored_asset["title"] == "Original verified gift"
                assert stored_asset["status"] == "verified"
                assert stored_listing["status"] == "active"
                assert stored_offer["status"] == "pending"
                assert history_count == 0

                with database.db() as conn:
                    conn.execute("DROP TRIGGER test_fail_ownership_history ON asset_ownership_history")
                    conn.execute("DROP FUNCTION test_fail_ownership_history()")

                transferred = marketplace.register_verified_asset(
                    source="ton_nft",
                    external_asset_id="ton-nft-transfer-atomicity",
                    owner_telegram_id="202",
                    title="New verified gift owner",
                    asset_type="gift",
                    image_url="",
                    verification_method="ton_connect_proof",
                    verification_reference="successful-owner-transfer-verification-reference",
                    allow_owner_transfer=True,
                )
                assert transferred["owner_telegram_id"] == "202"

                with database.db() as conn:
                    final_asset = conn.execute(
                        "SELECT owner_telegram_id FROM verified_assets WHERE id=?",
                        (asset["id"],),
                    ).fetchone()
                    final_listing = conn.execute(
                        "SELECT status FROM marketplace_orders WHERE id=?", (order_id,)
                    ).fetchone()
                    final_offer = conn.execute(
                        "SELECT status FROM marketplace_offers WHERE id=?", (offer_id,)
                    ).fetchone()
                    final_history_count = conn.execute(
                        "SELECT COUNT(*) FROM asset_ownership_history WHERE asset_id=?",
                        (asset["id"],),
                    ).fetchone()[0]

                assert final_asset["owner_telegram_id"] == "202"
                assert final_listing["status"] == "cancelled"
                assert final_offer["status"] == "declined"
                assert final_history_count == 1

    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')



def test_postgres_concurrent_duplicate_offers_are_serialized_per_listing():
    """Two simultaneous offers from one buyer must not create duplicate pending rows."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Barrier
    from unittest.mock import patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    import psycopg
    import database
    import marketplace
    import marketplace_offers
    from marketplace import create_marketplace_router
    from marketplace_offers import create_marketplace_offers_router

    schema = "offer_race_" + uuid.uuid4().hex[:10]
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')

    @contextmanager
    def scoped_connection():
        connection = database.connect()
        try:
            yield connection
        finally:
            connection.close()

    try:
        with patch.dict(os.environ, {
            "HASSLE_NFT_DB_BACKEND": "postgres",
            "DATABASE_URL": dsn,
            "HASSLE_POSTGRES_SCHEMA": schema,
            "HASSLE_VERIFIED_ASSETS_ENABLED": "true",
        }, clear=False), patch.object(
            marketplace, "_db", side_effect=scoped_connection
        ), patch.object(
            marketplace_offers, "_db", side_effect=scoped_connection
        ):
            marketplace.init_marketplace_db()
            marketplace_offers.init_marketplace_offers_db()
            marketplace.upsert_telegram_user({
                "id": "101", "username": "seller", "first_name": "Seller",
            })
            marketplace.upsert_telegram_user({
                "id": "202", "username": "buyer", "first_name": "Buyer",
            })
            marketplace.upsert_telegram_user({
                "id": "303", "username": "outsider", "first_name": "Outsider",
            })
            asset = marketplace.register_verified_asset(
                source="ton_nft",
                external_asset_id="ton-nft-concurrent-offer",
                owner_telegram_id="101",
                title="Verified concurrency gift",
                asset_type="gift",
                image_url="",
                verification_method="ton_connect_proof",
                verification_reference="concurrency-owner-verification-reference",
            )

            api = FastAPI()
            def verify_user(value):
                user_id = {"seller": "101", "buyer": "202"}[value]
                return {
                    "id": user_id,
                    "first_name": "Seller" if user_id == "101" else "Buyer",
                    "username": "seller" if user_id == "101" else "buyer",
                }
            api.include_router(create_marketplace_router(verify_user))
            api.include_router(create_marketplace_offers_router(verify_user))

            with patch.object(
                marketplace, "_verified_asset_actions_enabled", return_value=True
            ), patch.object(
                marketplace, "_test_ton_owner_recheck_skipped", return_value=True
            ), TestClient(api) as client:
                listing_response = client.post(
                    "/api/marketplace/orders",
                    headers={"X-Telegram-Init-Data": "seller"},
                    json={
                        "kind": "listing",
                        "asset_id": asset["id"],
                        "title": "Verified concurrency gift",
                        "price": "5",
                        "currency": "TON",
                    },
                )
                assert listing_response.status_code == 201, listing_response.text
                order_id = listing_response.json()["item"]["id"]

                barrier = Barrier(2)
                def submit_offer():
                    barrier.wait(timeout=10)
                    return client.post(
                        "/api/marketplace/offers",
                        headers={"X-Telegram-Init-Data": "buyer"},
                        json={"order_id": order_id, "amount": "4"},
                    )

                with ThreadPoolExecutor(max_workers=2) as executor:
                    responses = list(executor.map(lambda _index: submit_offer(), range(2)))

            assert sorted(response.status_code for response in responses) == [201, 409]
            with database.db() as conn:
                offer_rows = conn.execute(
                    "SELECT status, COUNT(*) AS total FROM marketplace_offers "
                    "WHERE order_id=? AND sender_user_id=? GROUP BY status",
                    (order_id, "202"),
                ).fetchall()
                total_offers = conn.execute(
                    "SELECT COUNT(*) FROM marketplace_offers WHERE order_id=? AND sender_user_id=?",
                    (order_id, "202"),
                ).fetchone()[0]

            assert total_offers == 1
            assert len(offer_rows) == 1
            assert offer_rows[0]["status"] == "pending"
            assert offer_rows[0]["total"] == 1

    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')



def test_postgres_offer_creation_racing_listing_cancel_leaves_no_pending_offer():
    """A concurrent offer/cancel race must never leave a pending offer on a cancelled listing."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Barrier
    from unittest.mock import patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    import psycopg
    import database
    import marketplace
    import marketplace_offers
    from marketplace import create_marketplace_router
    from marketplace_offers import create_marketplace_offers_router

    schema = "offer_cancel_race_" + uuid.uuid4().hex[:10]
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')

    @contextmanager
    def scoped_connection():
        connection = database.connect()
        try:
            yield connection
        finally:
            connection.close()

    try:
        with patch.dict(os.environ, {
            "HASSLE_NFT_DB_BACKEND": "postgres",
            "DATABASE_URL": dsn,
            "HASSLE_POSTGRES_SCHEMA": schema,
            "HASSLE_VERIFIED_ASSETS_ENABLED": "true",
        }, clear=False), patch.object(
            marketplace, "_db", side_effect=scoped_connection
        ), patch.object(
            marketplace_offers, "_db", side_effect=scoped_connection
        ):
            marketplace.init_marketplace_db()
            marketplace_offers.init_marketplace_offers_db()
            marketplace.upsert_telegram_user({
                "id": "101", "username": "seller", "first_name": "Seller",
            })
            marketplace.upsert_telegram_user({
                "id": "202", "username": "buyer", "first_name": "Buyer",
            })
            asset = marketplace.register_verified_asset(
                source="ton_nft",
                external_asset_id="ton-nft-offer-cancel-race",
                owner_telegram_id="101",
                title="Verified offer-cancel race gift",
                asset_type="gift",
                image_url="",
                verification_method="ton_connect_proof",
                verification_reference="offer-cancel-race-owner-verification",
            )

            api = FastAPI()
            def verify_user(value):
                user_id = {"seller": "101", "buyer": "202"}[value]
                return {
                    "id": user_id,
                    "first_name": "Seller" if user_id == "101" else "Buyer",
                    "username": "seller" if user_id == "101" else "buyer",
                }
            api.include_router(create_marketplace_router(verify_user))
            api.include_router(create_marketplace_offers_router(verify_user))

            with patch.object(
                marketplace, "_verified_asset_actions_enabled", return_value=True
            ), patch.object(
                marketplace, "_test_ton_owner_recheck_skipped", return_value=True
            ), TestClient(api) as client:
                listing_response = client.post(
                    "/api/marketplace/orders",
                    headers={"X-Telegram-Init-Data": "seller"},
                    json={
                        "kind": "listing",
                        "asset_id": asset["id"],
                        "title": "Verified offer-cancel race gift",
                        "price": "5",
                        "currency": "TON",
                    },
                )
                assert listing_response.status_code == 201, listing_response.text
                order_id = listing_response.json()["item"]["id"]

                barrier = Barrier(2)
                def create_offer():
                    barrier.wait(timeout=10)
                    return client.post(
                        "/api/marketplace/offers",
                        headers={"X-Telegram-Init-Data": "buyer"},
                        json={"order_id": order_id, "amount": "4"},
                    )
                def cancel_listing():
                    barrier.wait(timeout=10)
                    return client.delete(
                        f"/api/marketplace/orders/{order_id}",
                        headers={"X-Telegram-Init-Data": "seller"},
                    )

                with ThreadPoolExecutor(max_workers=2) as executor:
                    offer_future = executor.submit(create_offer)
                    cancel_future = executor.submit(cancel_listing)
                    offer_response = offer_future.result(timeout=20)
                    cancel_response = cancel_future.result(timeout=20)

            assert cancel_response.status_code == 200, cancel_response.text
            assert cancel_response.json()["item"]["status"] == "cancelled"
            assert offer_response.status_code in {201, 409}, offer_response.text

            with database.db() as conn:
                order = conn.execute(
                    "SELECT status FROM marketplace_orders WHERE id=?", (order_id,)
                ).fetchone()
                offers = conn.execute(
                    "SELECT status FROM marketplace_offers WHERE order_id=?", (order_id,)
                ).fetchall()
                cancellation_events = conn.execute(
                    "SELECT COUNT(*) FROM marketplace_order_events "
                    "WHERE order_id=? AND event_type='cancelled'",
                    (order_id,),
                ).fetchone()[0]

            assert order["status"] == "cancelled"
            assert cancellation_events == 1
            assert len(offers) <= 1
            assert all(row["status"] == "declined" for row in offers)

    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')



def test_postgres_concurrent_duplicate_listings_are_serialized_per_asset():
    """Two concurrent listing attempts for one verified NFT must produce one active listing."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Barrier
    from unittest.mock import patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    import psycopg
    import database
    import marketplace
    from marketplace import create_marketplace_router

    schema = "listing_race_" + uuid.uuid4().hex[:10]
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')

    @contextmanager
    def scoped_connection():
        connection = database.connect()
        try:
            yield connection
        finally:
            connection.close()

    try:
        with patch.dict(os.environ, {
            "HASSLE_NFT_DB_BACKEND": "postgres",
            "DATABASE_URL": dsn,
            "HASSLE_POSTGRES_SCHEMA": schema,
            "HASSLE_VERIFIED_ASSETS_ENABLED": "true",
        }, clear=False), patch.object(
            marketplace, "_db", side_effect=scoped_connection
        ):
            marketplace.init_marketplace_db()
            marketplace.upsert_telegram_user({
                "id": "101", "username": "seller", "first_name": "Seller",
            })
            asset = marketplace.register_verified_asset(
                source="ton_nft",
                external_asset_id="ton-nft-duplicate-listing-race",
                owner_telegram_id="101",
                title="Verified duplicate-listing gift",
                asset_type="gift",
                image_url="",
                verification_method="ton_connect_proof",
                verification_reference="duplicate-listing-race-verification-reference",
            )

            api = FastAPI()
            def verify_user(_value):
                return {"id": "101", "first_name": "Seller", "username": "seller"}
            api.include_router(create_marketplace_router(verify_user))

            with patch.object(
                marketplace, "_verified_asset_actions_enabled", return_value=True
            ), patch.object(
                marketplace, "_test_ton_owner_recheck_skipped", return_value=True
            ), TestClient(api) as client:
                barrier = Barrier(2)
                def create_listing():
                    barrier.wait(timeout=10)
                    return client.post(
                        "/api/marketplace/orders",
                        headers={"X-Telegram-Init-Data": "seller"},
                        json={
                            "kind": "listing",
                            "asset_id": asset["id"],
                            "title": "Client title is ignored",
                            "price": "5",
                            "currency": "TON",
                        },
                    )

                with ThreadPoolExecutor(max_workers=2) as executor:
                    responses = list(executor.map(lambda _index: create_listing(), range(2)))

            assert sorted(response.status_code for response in responses) == [201, 409]
            with database.db() as conn:
                active_count = conn.execute(
                    "SELECT COUNT(*) FROM marketplace_orders WHERE asset_id=? AND status='active'",
                    (asset["id"],),
                ).fetchone()[0]
                total_count = conn.execute(
                    "SELECT COUNT(*) FROM marketplace_orders WHERE asset_id=?",
                    (asset["id"],),
                ).fetchone()[0]

            assert active_count == 1
            assert total_count == 1

    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')



def test_postgres_concurrent_duplicate_deal_cancellations_release_reservation_once():
    """Both participants may retry cancellation, but only one lifecycle event is recorded."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Barrier
    from unittest.mock import patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    import psycopg
    import database
    import marketplace
    import marketplace_offers
    from marketplace import create_marketplace_router
    from marketplace_offers import create_marketplace_offers_router

    schema = "deal_cancel_race_" + uuid.uuid4().hex[:10]
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')

    @contextmanager
    def scoped_connection():
        connection = database.connect()
        try:
            yield connection
        finally:
            connection.close()

    try:
        with patch.dict(os.environ, {
            "HASSLE_NFT_DB_BACKEND": "postgres",
            "DATABASE_URL": dsn,
            "HASSLE_POSTGRES_SCHEMA": schema,
            "HASSLE_VERIFIED_ASSETS_ENABLED": "true",
        }, clear=False), patch.object(
            marketplace, "_db", side_effect=scoped_connection
        ), patch.object(
            marketplace_offers, "_db", side_effect=scoped_connection
        ):
            marketplace.init_marketplace_db()
            marketplace_offers.init_marketplace_offers_db()
            marketplace.upsert_telegram_user({
                "id": "101", "username": "seller", "first_name": "Seller",
            })
            marketplace.upsert_telegram_user({
                "id": "202", "username": "buyer", "first_name": "Buyer",
            })
            asset = marketplace.register_verified_asset(
                source="ton_nft",
                external_asset_id="ton-nft-deal-cancel-idempotency",
                owner_telegram_id="101",
                title="Verified deal cancellation gift",
                asset_type="gift",
                image_url="",
                verification_method="ton_connect_proof",
                verification_reference="deal-cancel-idempotency-verification",
            )

            api = FastAPI()
            def verify_user(value):
                user_id = {"seller": "101", "buyer": "202"}[value]
                return {
                    "id": user_id,
                    "first_name": "Seller" if user_id == "101" else "Buyer",
                    "username": "seller" if user_id == "101" else "buyer",
                }
            api.include_router(create_marketplace_router(verify_user))
            api.include_router(create_marketplace_offers_router(verify_user))

            with patch.object(
                marketplace, "_verified_asset_actions_enabled", return_value=True
            ), patch.object(
                marketplace, "_test_ton_owner_recheck_skipped", return_value=True
            ), TestClient(api) as client:
                listing = client.post(
                    "/api/marketplace/orders",
                    headers={"X-Telegram-Init-Data": "seller"},
                    json={
                        "kind": "listing", "asset_id": asset["id"],
                        "title": "Verified deal cancellation gift",
                        "price": "5", "currency": "TON",
                    },
                )
                assert listing.status_code == 201, listing.text
                order_id = listing.json()["item"]["id"]

                offer = client.post(
                    "/api/marketplace/offers",
                    headers={"X-Telegram-Init-Data": "buyer"},
                    json={"order_id": order_id, "amount": "4"},
                )
                assert offer.status_code == 201, offer.text
                offer_id = offer.json()["item"]["id"]
                deal_id = "deal-" + uuid.uuid4().hex[:16]
                now = int(time.time())

                with database.db() as conn:
                    conn.execute(
                        "UPDATE marketplace_orders SET status='matched', updated_at=? WHERE id=?",
                        (now, order_id),
                    )
                    conn.execute(
                        "UPDATE marketplace_offers SET status='accepted', updated_at=? WHERE id=?",
                        (now, offer_id),
                    )
                    conn.execute(
                        """INSERT INTO marketplace_deals
                           (id, order_id, offer_id, listing_owner_id, offer_sender_id,
                            agreed_amount, currency, created_at, status)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'payment_not_enabled')""",
                        (deal_id, order_id, offer_id, "101", "202", "4", "TON", now),
                    )

                barrier = Barrier(2)
                def cancel_as(token):
                    barrier.wait(timeout=10)
                    return client.post(
                        f"/api/marketplace/deals/{deal_id}/actions",
                        headers={"X-Telegram-Init-Data": token},
                        json={"action": "cancel"},
                    )

                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(cancel_as, "seller"),
                        executor.submit(cancel_as, "buyer"),
                    ]
                    responses = [future.result(timeout=20) for future in futures]

                seller_events = client.get(
                    f"/api/marketplace/deals/{deal_id}/events",
                    headers={"X-Telegram-Init-Data": "seller"},
                )
                buyer_events = client.get(
                    f"/api/marketplace/deals/{deal_id}/events",
                    headers={"X-Telegram-Init-Data": "buyer"},
                )
                outsider_events = client.get(
                    f"/api/marketplace/deals/{deal_id}/events",
                    headers={"X-Telegram-Init-Data": "outsider"},
                )

            assert seller_events.status_code == 200, seller_events.text
            assert buyer_events.status_code == 200, buyer_events.text
            assert outsider_events.status_code == 404
            assert seller_events.json() == buyer_events.json()
            event_items = seller_events.json()["items"]
            assert [item["type"] for item in event_items] == ["deal_cancelled"]
            assert event_items[0]["data"]["reservation_released"] is True
            assert seller_events.json()["total"] == 1

            assert [response.status_code for response in responses] == [200, 200], [
                response.text for response in responses
            ]
            payloads = [response.json() for response in responses]
            assert all(item["status"] == "cancelled" for item in payloads)
            assert sorted(item.get("already_cancelled", False) for item in payloads) == [False, True]
            assert sum(bool(item.get("reservation_released")) for item in payloads) == 1

            with database.db() as conn:
                deal = conn.execute(
                    "SELECT status FROM marketplace_deals WHERE id=?", (deal_id,)
                ).fetchone()
                order = conn.execute(
                    "SELECT status FROM marketplace_orders WHERE id=?", (order_id,)
                ).fetchone()
                events = conn.execute(
                    "SELECT COUNT(*) FROM marketplace_deal_lifecycle_events "
                    "WHERE deal_id=? AND event_type='deal_cancelled'",
                    (deal_id,),
                ).fetchone()[0]

            assert deal["status"] == "cancelled"
            assert order["status"] == "active"
            assert events == 1

    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
