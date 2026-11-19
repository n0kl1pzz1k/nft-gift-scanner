import os
import sqlite3
import uuid

import pytest
import psycopg
from psycopg import sql

from db_postgres_schema_apply import apply_schema


def test_schema_apply_creates_and_verifies_postgres_schema_atomically(tmp_path):
    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    source_path = tmp_path / "schema.sqlite3"
    source = sqlite3.connect(source_path)
    source.executescript(
        """
        CREATE TABLE marketplace_deal_events (id TEXT PRIMARY KEY);
        CREATE TABLE child (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('open', 'closed')),
            FOREIGN KEY(event_id) REFERENCES marketplace_deal_events(id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX idx_child_open ON child(event_id) WHERE state = 'open';
        CREATE TRIGGER trg_marketplace_deal_events_no_delete BEFORE DELETE ON marketplace_deal_events
        BEGIN SELECT RAISE(ABORT, 'immutable'); END;
        """
    )
    source.commit()
    source.close()

    schema = "mig_schema_" + uuid.uuid4().hex[:12]
    with psycopg.connect(dsn, autocommit=True) as pg:
        pg.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        pg.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        try:
            dry_run = apply_schema(str(source_path), dsn, apply=False, target_schema=schema)
            assert dry_run["applied"] is False
            assert dry_run["blocker_count"] == 0
            applied = apply_schema(str(source_path), dsn, apply=True, target_schema=schema)
            assert applied["applied"] is True
            assert applied["verified"] is True
            assert applied["source_counts"]["tables"] == 2
            assert applied["target_counts"]["foreign_keys"] == 1
            assert applied["target_counts"]["check_constraints"] == 1
            assert applied["target_counts"]["indexes"] == applied["source_counts"]["indexes"]
            assert applied["target_counts"]["triggers"] == 1
        finally:
            pg.execute("SET search_path TO public")
            pg.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_schema_apply_refuses_unsupported_sqlite_view(tmp_path):
    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    source_path = tmp_path / "view.sqlite3"
    source = sqlite3.connect(source_path)
    source.executescript("CREATE TABLE sample (id TEXT PRIMARY KEY); CREATE VIEW sample_view AS SELECT id FROM sample;")
    source.close()

    with pytest.raises(RuntimeError, match="blocker"):
        apply_schema(str(source_path), dsn, apply=False)


def test_schema_apply_preserves_all_marketplace_trigger_behaviors(tmp_path):
    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    source_path = tmp_path / "marketplace_triggers.sqlite3"
    source = sqlite3.connect(source_path)
    source.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE marketplace_orders (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
            owner_name TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE marketplace_offers (
            id TEXT PRIMARY KEY, order_id TEXT NOT NULL, sender_user_id TEXT NOT NULL,
            sender_name TEXT NOT NULL, amount TEXT NOT NULL DEFAULT '1',
            currency TEXT NOT NULL DEFAULT 'TON' CHECK(currency IN ('TON','XTR')),
            note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            FOREIGN KEY(order_id) REFERENCES marketplace_orders(id)
        );
        CREATE TABLE marketplace_notifications (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, event_type TEXT NOT NULL,
            offer_id TEXT NOT NULL, order_id TEXT NOT NULL, order_title TEXT NOT NULL DEFAULT '',
            actor_name TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, read_at INTEGER,
            FOREIGN KEY(offer_id) REFERENCES marketplace_offers(id),
            FOREIGN KEY(order_id) REFERENCES marketplace_orders(id)
        );
        CREATE TABLE marketplace_deal_events (
            id TEXT PRIMARY KEY, deal_id TEXT NOT NULL, order_id TEXT NOT NULL,
            actor_user_id TEXT NOT NULL, event_type TEXT NOT NULL,
            event_data TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL
        );
        CREATE TABLE marketplace_deal_lifecycle_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            deal_id TEXT NOT NULL, order_id TEXT NOT NULL, actor_user_id TEXT NOT NULL,
            event_type TEXT NOT NULL, event_data TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL
        );
        CREATE TABLE verified_assets (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, external_asset_id TEXT NOT NULL,
            owner_telegram_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
            asset_type TEXT NOT NULL DEFAULT '', image_url TEXT NOT NULL DEFAULT '',
            verification_method TEXT NOT NULL DEFAULT '',
            verification_reference_hash TEXT NOT NULL DEFAULT '', verified_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'verified'
        );
        CREATE TABLE asset_owner_transfer_authorizations (
            asset_id TEXT PRIMARY KEY REFERENCES verified_assets(id),
            previous_owner_id TEXT NOT NULL, new_owner_id TEXT NOT NULL,
            verification_reference_hash TEXT NOT NULL, created_at INTEGER NOT NULL
        );

        CREATE TRIGGER trg_marketplace_deal_events_no_update
        BEFORE UPDATE ON marketplace_deal_events BEGIN
          SELECT RAISE(ABORT, 'deal events are immutable');
        END;
        CREATE TRIGGER trg_marketplace_deal_events_no_delete
        BEFORE DELETE ON marketplace_deal_events BEGIN
          SELECT RAISE(ABORT, 'deal events are immutable');
        END;
        CREATE TRIGGER trg_marketplace_deal_lifecycle_events_no_update
        BEFORE UPDATE ON marketplace_deal_lifecycle_events BEGIN
          SELECT RAISE(ABORT, 'deal lifecycle events are immutable');
        END;
        CREATE TRIGGER trg_marketplace_deal_lifecycle_events_no_delete
        BEFORE DELETE ON marketplace_deal_lifecycle_events BEGIN
          SELECT RAISE(ABORT, 'deal lifecycle events are immutable');
        END;
        CREATE TRIGGER trg_marketplace_offer_received
        AFTER INSERT ON marketplace_offers BEGIN
          INSERT INTO marketplace_notifications
            (id,user_id,event_type,offer_id,order_id,order_title,actor_name,created_at)
          SELECT 'nt-' || lower(hex(randomblob(8))), o.user_id, 'offer_received',
                 NEW.id, NEW.order_id, o.title, NEW.sender_name, NEW.created_at
          FROM marketplace_orders o
          WHERE o.id=NEW.order_id AND o.user_id<>NEW.sender_user_id;
        END;
        CREATE TRIGGER trg_marketplace_offer_status_notification
        AFTER UPDATE OF status ON marketplace_offers
        WHEN OLD.status='pending' AND NEW.status<>OLD.status BEGIN
          INSERT INTO marketplace_notifications
            (id,user_id,event_type,offer_id,order_id,order_title,actor_name,created_at)
          SELECT 'nt-' || lower(hex(randomblob(8))),
                 CASE WHEN NEW.status='withdrawn' THEN o.user_id ELSE NEW.sender_user_id END,
                 CASE NEW.status WHEN 'accepted' THEN 'offer_accepted'
                   WHEN 'declined' THEN 'offer_declined' WHEN 'withdrawn' THEN 'offer_withdrawn' END,
                 NEW.id,NEW.order_id,o.title,
                 CASE WHEN NEW.status='withdrawn' THEN NEW.sender_name ELSE o.owner_name END,
                 NEW.updated_at
          FROM marketplace_orders o
          WHERE o.id=NEW.order_id AND NEW.status IN ('accepted','declined','withdrawn')
            AND (NEW.status<>'withdrawn' OR o.user_id<>NEW.sender_user_id);
        END;
        CREATE TRIGGER trg_marketplace_order_cancel_declines_offers
        AFTER UPDATE OF status ON marketplace_orders
        WHEN OLD.status='active' AND NEW.status='cancelled' BEGIN
          UPDATE marketplace_offers SET status='declined',updated_at=NEW.updated_at
          WHERE order_id=NEW.id AND status='pending';
        END;
        CREATE TRIGGER verified_assets_metadata_immutable
        BEFORE UPDATE ON verified_assets
        WHEN NOT (
          NEW.status='verified' AND NEW.owner_telegram_id<>OLD.owner_telegram_id
          AND NEW.source=OLD.source AND NEW.external_asset_id=OLD.external_asset_id
        )
        BEGIN SELECT RAISE(ABORT, 'verified asset metadata is immutable'); END;
        """
    )
    source.commit()
    source.close()

    schema = "trigger_schema_" + uuid.uuid4().hex[:12]
    with psycopg.connect(dsn, autocommit=True) as pg:
        pg.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        pg.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        try:
            applied = apply_schema(str(source_path), dsn, apply=True, target_schema=schema)
            assert applied["verified"] is True
            assert applied["target_counts"]["triggers"] == 9

            with psycopg.connect(dsn) as target:
                target.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
                target.execute(
                    "INSERT INTO marketplace_orders VALUES (%s,%s,%s,%s,%s,%s)",
                    ("order-1", "seller", "Test gift", "Seller", "active", 100),
                )
                target.execute(
                    "INSERT INTO marketplace_offers (id,order_id,sender_user_id,sender_name,status,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    ("offer-1", "order-1", "buyer", "Buyer", "pending", 101, 101),
                )
                received = target.execute(
                    "SELECT user_id,event_type,order_title,actor_name FROM marketplace_notifications WHERE offer_id=%s",
                    ("offer-1",),
                ).fetchone()
                assert received == ("seller", "offer_received", "Test gift", "Buyer")

                target.execute(
                    "UPDATE marketplace_offers SET status='accepted',updated_at=102 WHERE id='offer-1'"
                )
                accepted = target.execute(
                    "SELECT user_id,event_type,actor_name FROM marketplace_notifications WHERE offer_id='offer-1' AND event_type='offer_accepted'"
                ).fetchone()
                assert accepted == ("buyer", "offer_accepted", "Seller")

                target.execute(
                    "INSERT INTO marketplace_offers (id,order_id,sender_user_id,sender_name,status,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    ("offer-withdrawn", "order-1", "buyer-3", "Buyer Three", "pending", 102, 102),
                )
                target.execute(
                    "UPDATE marketplace_offers SET status='withdrawn',updated_at=103 WHERE id='offer-withdrawn'"
                )
                withdrawn = target.execute(
                    "SELECT user_id,event_type,actor_name FROM marketplace_notifications WHERE offer_id='offer-withdrawn' AND event_type='offer_withdrawn'"
                ).fetchone()
                assert withdrawn == ("seller", "offer_withdrawn", "Buyer Three")

                target.execute(
                    "INSERT INTO marketplace_offers (id,order_id,sender_user_id,sender_name,status,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    ("offer-declined", "order-1", "buyer-4", "Buyer Four", "pending", 103, 103),
                )
                target.execute(
                    "UPDATE marketplace_offers SET status='declined',updated_at=104 WHERE id='offer-declined'"
                )
                direct_decline = target.execute(
                    "SELECT user_id,event_type,actor_name FROM marketplace_notifications WHERE offer_id='offer-declined' AND event_type='offer_declined'"
                ).fetchone()
                assert direct_decline == ("buyer-4", "offer_declined", "Seller")

                target.execute(
                    "INSERT INTO marketplace_offers (id,order_id,sender_user_id,sender_name,status,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    ("offer-2", "order-1", "buyer-2", "Buyer Two", "pending", 105, 105),
                )
                target.execute(
                    "UPDATE marketplace_orders SET status='cancelled',updated_at=104 WHERE id='order-1'"
                )
                declined = target.execute(
                    "SELECT status FROM marketplace_offers WHERE id='offer-2'"
                ).fetchone()
                assert declined == ("declined",)

                target.execute(
                    "INSERT INTO marketplace_deal_events VALUES ('event-1','deal-1','order-1','seller','deal_created','{}',105)"
                )
                with pytest.raises(psycopg.Error):
                    with target.transaction():
                        target.execute("UPDATE marketplace_deal_events SET event_type='changed' WHERE id='event-1'")
                with pytest.raises(psycopg.Error):
                    with target.transaction():
                        target.execute("DELETE FROM marketplace_deal_events WHERE id='event-1'")

                target.execute(
                    "INSERT INTO marketplace_deal_lifecycle_events (id,deal_id,order_id,actor_user_id,event_type,created_at) VALUES ('life-1','deal-1','order-1','seller','deal_cancelled',106)"
                )
                with pytest.raises(psycopg.Error):
                    with target.transaction():
                        target.execute("UPDATE marketplace_deal_lifecycle_events SET event_type='changed' WHERE id='life-1'")
                with pytest.raises(psycopg.Error):
                    with target.transaction():
                        target.execute("DELETE FROM marketplace_deal_lifecycle_events WHERE id='life-1'")

                target.execute(
                    "INSERT INTO verified_assets (id,source,external_asset_id,owner_telegram_id,verified_at) VALUES ('asset-1','telegram','gift-1','seller',107)"
                )
                with pytest.raises(psycopg.Error):
                    with target.transaction():
                        target.execute("UPDATE verified_assets SET title='tampered' WHERE id='asset-1'")
                with pytest.raises(psycopg.Error):
                    with target.transaction():
                        target.execute(
                            "UPDATE verified_assets SET owner_telegram_id='buyer' WHERE id='asset-1'"
                        )
                target.execute(
                    "INSERT INTO asset_owner_transfer_authorizations(asset_id,previous_owner_id,new_owner_id,verification_reference_hash,created_at) VALUES ('asset-1','seller','buyer','verified-hash',108)"
                )
                target.execute(
                    "UPDATE verified_assets SET owner_telegram_id='buyer', verification_reference_hash='verified-hash', status='verified' WHERE id='asset-1'"
                )
                assert target.execute(
                    "SELECT owner_telegram_id FROM verified_assets WHERE id='asset-1'"
                ).fetchone() == ("buyer",)
                assert target.execute(
                    "SELECT COUNT(*) FROM asset_owner_transfer_authorizations WHERE asset_id='asset-1'"
                ).fetchone() == (0,)
        finally:
            pg.execute("SET search_path TO public")
            pg.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_schema_apply_rolls_back_all_ddl_when_a_later_statement_fails(tmp_path, monkeypatch):
    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    source_path = tmp_path / "rollback.sqlite3"
    source = sqlite3.connect(source_path)
    source.execute("CREATE TABLE source_sample (id TEXT PRIMARY KEY)")
    source.commit()
    source.close()

    schema = "mig_rollback_" + uuid.uuid4().hex[:12]
    with psycopg.connect(dsn, autocommit=True) as pg:
        pg.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        monkeypatch.setattr(
            "db_postgres_schema_apply.build_postgres_ddl_plan",
            lambda manifest, data_preflight: {
                "blocker_count": 0,
                "statement_count": 2,
                "statements": [
                    'CREATE TABLE "rollback_probe" ("id" BIGINT PRIMARY KEY)',
                    "THIS IS INTENTIONALLY INVALID SQL",
                ],
            },
        )

        with pytest.raises(psycopg.Error):
            apply_schema(str(source_path), dsn, apply=True, target_schema=schema)

        with psycopg.connect(dsn) as pg:
            remaining = pg.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema=%s AND table_name='rollback_probe'",
                (schema,),
            ).fetchone()[0]
            assert remaining == 0
    finally:
        with psycopg.connect(dsn, autocommit=True) as pg:
            pg.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
