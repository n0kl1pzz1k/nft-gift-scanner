import sqlite3

import psycopg
import pytest
from psycopg import sql

from db_postgres_shadow_migration import run_shadow_migration


def test_shadow_migration_creates_schema_and_copies_all_rows_atomically(tmp_path):
    import os
    import uuid

    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    source_path = tmp_path / "source.sqlite3"
    source = sqlite3.connect(source_path)
    source.execute("CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT NOT NULL)")
    source.execute("INSERT INTO items(value) VALUES ('first')")
    source.execute("INSERT INTO items(value) VALUES ('deleted-high-water')")
    source.execute("DELETE FROM items WHERE id = 2")
    source.commit()
    source.close()

    backup_path = tmp_path / "shadow-backup.sqlite3"
    schema = "mig_shadow_" + uuid.uuid4().hex[:12]
    with psycopg.connect(dsn, autocommit=True) as pg:
        pg.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            dry = run_shadow_migration(str(source_path), str(backup_path), dsn, execute=False, target_schema=schema)
            assert dry["restore_verified"] is True
            assert dry["preflight_exact_match"] is True
            assert dry["migration"]["committed"] is False
            assert dry["migration"]["target_schema_empty"] is True

            report = run_shadow_migration(
                str(source_path), str(tmp_path / "shadow-backup-execute.sqlite3"),
                dsn, execute=True, target_schema=schema,
            )
            assert report["migration"]["committed"] is True
            assert report["migration"]["verified"] is True
            assert report["migration"]["tables"][0]["content_verified"] is True
            assert report["production_app_switched"] is False
            pg.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            rows = pg.execute("SELECT id, value FROM items").fetchall()
            next_id = pg.execute("SELECT nextval(pg_get_serial_sequence('items', 'id'))").fetchone()[0]
            assert rows == [(1, "first")]
            assert next_id == 3
        finally:
            pg.execute("SET search_path TO public")
            pg.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_shadow_migration_rolls_back_schema_and_rows_on_copy_failure(tmp_path):
    import os
    import uuid

    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    source_path = tmp_path / "bad-source.sqlite3"
    source = sqlite3.connect(source_path)
    source.executescript(
        """
        CREATE TABLE a_items (id TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE b_items (id TEXT PRIMARY KEY, value INTEGER NOT NULL);
        INSERT INTO a_items VALUES ('a1', 'valid');
        INSERT INTO b_items VALUES ('b1', 'not-an-integer');
        """
    )
    source.commit()
    source.close()

    schema = "mig_shadow_rollback_" + uuid.uuid4().hex[:10]
    with psycopg.connect(dsn, autocommit=True) as pg:
        pg.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            with pytest.raises(psycopg.Error):
                run_shadow_migration(
                    str(source_path), str(tmp_path / "bad-backup.sqlite3"),
                    dsn, execute=True, target_schema=schema,
                )
            pg.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            remaining = pg.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE'"
            ).fetchone()[0]
            assert remaining == 0
        finally:
            pg.execute("SET search_path TO public")
            pg.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_shadow_migration_refuses_nonexistent_target_schema(tmp_path):
    import os
    import uuid

    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    source_path = tmp_path / "missing-schema-source.sqlite3"
    source = sqlite3.connect(source_path)
    source.execute("CREATE TABLE items (id TEXT PRIMARY KEY, value TEXT)")
    source.execute("INSERT INTO items VALUES ('one', 'value')")
    source.commit()
    source.close()

    missing_schema = "missing_" + uuid.uuid4().hex[:10]
    with pytest.raises(RuntimeError, match="target schema does not exist"):
        run_shadow_migration(
            str(source_path),
            str(tmp_path / "missing-schema-backup.sqlite3"),
            dsn,
            execute=False,
            target_schema=missing_schema,
        )


def test_shadow_copy_does_not_replay_historical_offer_notifications(tmp_path):
    import os
    import uuid

    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    source_path = tmp_path / "offers.sqlite3"
    source = sqlite3.connect(source_path)
    source.executescript(
        """
        CREATE TABLE marketplace_orders (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL,
            owner_name TEXT NOT NULL, status TEXT NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE TABLE marketplace_offers (
            id TEXT PRIMARY KEY, order_id TEXT NOT NULL, sender_user_id TEXT NOT NULL,
            sender_name TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL, FOREIGN KEY(order_id) REFERENCES marketplace_orders(id)
        );
        CREATE TABLE marketplace_notifications (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, event_type TEXT NOT NULL,
            offer_id TEXT, order_id TEXT, order_title TEXT, actor_name TEXT, created_at INTEGER NOT NULL
        );
        INSERT INTO marketplace_orders VALUES ('order1', 'seller', 'Gift', 'Seller', 'active', 10);
        INSERT INTO marketplace_offers VALUES ('offer1', 'order1', 'buyer', 'Buyer', 'pending', 11, 11);
        INSERT INTO marketplace_notifications VALUES ('notice1', 'seller', 'offer_received', 'offer1', 'order1', 'Gift', 'Buyer', 11);
        CREATE TRIGGER trg_marketplace_offer_received AFTER INSERT ON marketplace_offers
        BEGIN
            INSERT INTO marketplace_notifications
                (id, user_id, event_type, offer_id, order_id, order_title, actor_name, created_at)
            SELECT 'nt-' || lower(hex(randomblob(8))), o.user_id, 'offer_received',
                   NEW.id, NEW.order_id, o.title, NEW.sender_name, NEW.created_at
            FROM marketplace_orders o
            WHERE o.id = NEW.order_id AND o.user_id <> NEW.sender_user_id;
        END;
        """
    )
    source.commit()
    source.close()

    schema = "mig_shadow_trigger_" + uuid.uuid4().hex[:10]
    with psycopg.connect(dsn, autocommit=True) as pg:
        pg.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            result = run_shadow_migration(
                str(source_path), str(tmp_path / "offers.backup.sqlite3"),
                dsn, execute=True, target_schema=schema,
            )
            assert result["migration"]["verified"] is True
            pg.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            assert pg.execute("SELECT COUNT(*) FROM marketplace_notifications").fetchone()[0] == 1

            pg.execute(
                "INSERT INTO marketplace_offers VALUES (%s, %s, %s, %s, %s, %s, %s)",
                ("offer2", "order1", "buyer2", "Buyer Two", "pending", 12, 12),
            )
            assert pg.execute(
                "SELECT COUNT(*) FROM marketplace_notifications"
            ).fetchone()[0] == 2
        finally:
            pg.execute("SET search_path TO public")
            pg.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
