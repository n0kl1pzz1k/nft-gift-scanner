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
