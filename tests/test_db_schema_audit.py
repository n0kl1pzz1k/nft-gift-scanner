import json
import sqlite3

import pytest

from db_schema_audit import audit_database, build_schema_manifest


def test_manifest_captures_constraints_indexes_and_triggers_without_rows():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE parent (id TEXT PRIMARY KEY, label TEXT NOT NULL);
        CREATE TABLE child (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('open', 'closed')),
            FOREIGN KEY(parent_id) REFERENCES parent(id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX idx_child_one_open
            ON child(parent_id) WHERE state = 'open';
        CREATE TRIGGER child_no_delete BEFORE DELETE ON child
        BEGIN SELECT RAISE(ABORT, 'immutable'); END;
        CREATE VIEW open_children AS SELECT * FROM child WHERE state = 'open';
        INSERT INTO parent VALUES ('p1', 'PRIVATE_ROW_SENTINEL');
        """
    )

    manifest = build_schema_manifest(connection)
    serialized = json.dumps(manifest, ensure_ascii=False)

    assert manifest["engine"] == "sqlite"
    assert manifest["schema_sha256"]
    assert "PRIVATE_ROW_SENTINEL" not in serialized
    child = next(table for table in manifest["tables"] if table["name"] == "child")
    assert child["foreign_keys"][0]["referenced_table"] == "parent"
    partial_index = next(index for index in child["indexes"] if index["name"] == "idx_child_one_open")
    assert partial_index["unique"] is True
    assert partial_index["partial"] is True
    assert {obj["type"] for obj in manifest["objects"]} == {"trigger", "view"}
    connection.close()


def test_manifest_hash_is_stable_for_unchanged_schema():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE stable (id TEXT PRIMARY KEY, created_at INTEGER NOT NULL)")
    first = build_schema_manifest(connection)
    second = build_schema_manifest(connection)
    assert first["schema_sha256"] == second["schema_sha256"]
    connection.close()


def test_audit_opens_existing_database_read_only(tmp_path):
    database = tmp_path / "readonly.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sample (id TEXT PRIMARY KEY)")
    connection.close()

    manifest = audit_database(database)
    assert [table["name"] for table in manifest["tables"]] == ["sample"]

    with pytest.raises(sqlite3.OperationalError):
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            connection.execute("CREATE TABLE forbidden (id TEXT)")
        finally:
            connection.close()


def test_current_hassle_schema_inventory_covers_all_subsystems(tmp_path, monkeypatch):
    import marketplace
    import demo_offers
    import ton_connect

    database = tmp_path / "hassle.sqlite3"
    monkeypatch.setattr(marketplace, "DB_PATH", str(database))
    monkeypatch.setattr(demo_offers, "DB_PATH", str(database))

    marketplace.init_marketplace_db()
    demo_offers.init_demo_offers_db()
    ton_connect.init_ton_connect_db()

    connection = sqlite3.connect(database)
    manifest = build_schema_manifest(connection)
    connection.close()

    names = {table["name"] for table in manifest["tables"]}
    expected = {
        "telegram_users", "user_profiles", "telegram_sessions",
        "telegram_initdata_uses", "telegram_update_inbox", "user_favorites",
        "demo_orders", "verified_assets", "demo_order_events",
        "demo_order_revisions", "demo_order_reports", "asset_ownership_history",
        "api_rate_limits", "app_settings", "telegram_business_connections",
        "telegram_business_gift_refs", "demo_offers", "demo_deals",
        "demo_deal_events", "demo_deal_lifecycle_events", "demo_notifications",
        "ton_proof_challenges", "verified_wallets",
    }
    assert expected <= names

    orders = next(table for table in manifest["tables"] if table["name"] == "demo_orders")
    assert {column["name"] for column in orders["columns"]} >= {"id", "asset_id", "asset_type"}
    deals = next(table for table in manifest["tables"] if table["name"] == "demo_deals")
    assert any(index["name"] == "idx_demo_deals_one_open_reservation" and index["partial"] for index in deals["indexes"])
    assert manifest["schema_sha256"]
