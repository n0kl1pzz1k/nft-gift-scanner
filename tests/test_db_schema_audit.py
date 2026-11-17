import json
import sqlite3


from db_schema_audit import audit_database, build_postgres_portability_report, build_runtime_inventory, build_schema_manifest


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

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        try:
            connection.execute("CREATE TABLE forbidden (id TEXT)")
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("read-only SQLite connection unexpectedly allowed a write")
    finally:
        connection.close()


def test_current_hassle_schema_inventory_covers_all_subsystems(tmp_path, monkeypatch):
    import marketplace
    import marketplace_offers
    import ton_connect

    database = tmp_path / "hassle.sqlite3"
    monkeypatch.setattr(marketplace, "DB_PATH", str(database))
    monkeypatch.setattr(marketplace_offers, "DB_PATH", str(database))

    marketplace.init_marketplace_db()
    marketplace_offers.init_marketplace_offers_db()
    ton_connect.init_ton_connect_db()

    connection = sqlite3.connect(database)
    manifest = build_schema_manifest(connection)
    connection.close()

    names = {table["name"] for table in manifest["tables"]}
    expected = {
        "telegram_users", "user_profiles", "telegram_sessions",
        "telegram_initdata_uses", "telegram_update_inbox", "user_favorites",
        "marketplace_orders", "verified_assets", "marketplace_order_events",
        "marketplace_order_revisions", "marketplace_order_reports", "asset_ownership_history",
        "api_rate_limits", "app_settings", "telegram_business_connections",
        "telegram_business_gift_refs", "marketplace_offers", "marketplace_deals",
        "marketplace_deal_events", "marketplace_deal_lifecycle_events", "marketplace_notifications",
        "ton_proof_challenges", "verified_wallets",
    }
    assert expected <= names

    orders = next(table for table in manifest["tables"] if table["name"] == "marketplace_orders")
    assert {column["name"] for column in orders["columns"]} >= {"id", "asset_id", "asset_type"}
    deals = next(table for table in manifest["tables"] if table["name"] == "marketplace_deals")
    assert any(index["name"] == "idx_marketplace_deals_one_open_reservation" and index["partial"] for index in deals["indexes"])
    assert manifest["schema_sha256"]



def test_postgres_portability_report_flags_sqlite_only_schema_features():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE parent (id TEXT PRIMARY KEY);
        CREATE TABLE child (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('open', 'closed')),
            FOREIGN KEY(parent_id) REFERENCES parent(id)
        );
        CREATE UNIQUE INDEX idx_child_open
            ON child(parent_id) WHERE state = 'open';
        CREATE TRIGGER child_no_delete BEFORE DELETE ON child
        BEGIN SELECT RAISE(ABORT, 'immutable'); END;
        CREATE VIEW open_children AS SELECT * FROM child WHERE state = 'open';
        """
    )
    manifest = build_schema_manifest(connection)
    report = build_postgres_portability_report(manifest)
    connection.close()

    assert report["target_engine"] == "postgresql"
    assert report["ready_for_cutover"] is False
    assert report["table_count"] == 2
    assert report["foreign_key_count"] == 1
    assert report["trigger_count"] == 1
    assert report["view_count"] == 1
    assert report["partial_indexes"][0]["name"] == "idx_child_open"
    assert report["check_constraint_tables"] == [{"table": "child"}]
    blocker_features = {item["feature"] for item in report["blockers"]}
    assert blocker_features == {"sqlite_triggers", "sqlite_autoincrement"}


def test_postgres_portability_report_contains_no_table_rows():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE private_data (id TEXT PRIMARY KEY, secret TEXT NOT NULL);
        INSERT INTO private_data VALUES ('row-1', 'DO_NOT_EXPORT_THIS');
        """
    )
    report = build_postgres_portability_report(build_schema_manifest(connection))
    connection.close()

    rendered = json.dumps(report, ensure_ascii=False)
    assert "DO_NOT_EXPORT_THIS" not in rendered
    assert report["ready_for_cutover"] is False
    assert "transaction boundaries" in " ".join(report["review_items"])

def test_runtime_inventory_reports_counts_and_schema_without_row_values():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE accounts (id TEXT PRIMARY KEY, private_note TEXT NOT NULL);
        INSERT INTO accounts VALUES ('one', 'PRIVATE_ROW_SENTINEL');
        INSERT INTO accounts VALUES ('two', 'ANOTHER_PRIVATE_VALUE');
        """
    )
    inventory = build_runtime_inventory(connection)
    rendered = json.dumps(inventory, ensure_ascii=False)
    connection.close()

    assert inventory["table_count"] == 1
    assert inventory["total_rows"] == 2
    assert inventory["tables"][0]["name"] == "accounts"
    assert inventory["tables"][0]["row_count"] == 2
    assert "CREATE TABLE accounts" in inventory["tables"][0]["create_sql"]
    assert inventory["postgresql_portability"]["target_engine"] == "postgresql"
    assert [column["name"] for column in inventory["tables"][0]["columns"]] == ["id", "private_note"]
    assert "PRIVATE_ROW_SENTINEL" not in rendered
    assert "ANOTHER_PRIVATE_VALUE" not in rendered
    assert "DO_NOT_EXPORT_THIS" not in rendered

def test_postgres_portability_report_classifies_declared_sqlite_type_affinities():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE affinity_samples (
            integer_value INTEGER,
            text_value VARCHAR(255),
            blob_value BLOB,
            real_value DOUBLE PRECISION,
            numeric_value DECIMAL(12, 4),
            no_type
        )
        """
    )

    report = build_postgres_portability_report(build_schema_manifest(connection))
    connection.close()

    affinities = {
        item["column"]: item["sqlite_affinity"]
        for item in report["column_type_affinities"]
    }
    assert affinities == {
        "integer_value": "INTEGER",
        "text_value": "TEXT",
        "blob_value": "BLOB",
        "real_value": "REAL",
        "numeric_value": "NUMERIC",
        "no_type": "BLOB",
    }
    assert report["type_mapping_review_count"] == 6
    assert any("affinity is not a lossless type mapping" in item for item in report["review_items"])



def test_postgres_type_candidates_preserve_integer_range_and_guard_boolean_conversion():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE sample (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_update_id INTEGER NOT NULL,
            is_enabled INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            amount_text TEXT NOT NULL
        );
        """
    )

    report = build_postgres_portability_report(build_schema_manifest(connection))
    connection.close()
    candidates = {
        item["column"]: item
        for item in report["column_type_affinities"]
    }

    assert candidates["id"]["postgresql_type_candidate"] == "BIGINT GENERATED BY DEFAULT AS IDENTITY"
    assert candidates["telegram_update_id"]["postgresql_type_candidate"] == "BIGINT"
    assert candidates["created_at"]["postgresql_type_candidate"] == "BIGINT"
    assert candidates["is_enabled"]["postgresql_type_candidate"] == "BOOLEAN"
    assert candidates["is_enabled"]["mapping_confidence"] == "conditional"
    assert "exactly 0 or 1" in candidates["is_enabled"]["mapping_reason"]
    assert candidates["payload_json"]["postgresql_type_candidate"] == "TEXT"
    assert candidates["amount_text"]["postgresql_type_candidate"] == "TEXT"
    assert report["ready_for_cutover"] is False


def test_postgres_type_candidates_do_not_claim_json_or_numeric_text_conversion():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE values_to_preserve (gift_json TEXT NOT NULL, price TEXT NOT NULL)"
    )
    report = build_postgres_portability_report(build_schema_manifest(connection))
    connection.close()

    candidates = {item["column"]: item for item in report["column_type_affinities"]}
    assert candidates["gift_json"]["postgresql_type_candidate"] == "TEXT"
    assert "JSONB conversion requires separate validation" in candidates["gift_json"]["mapping_reason"]
    assert candidates["price"]["postgresql_type_candidate"] == "TEXT"


def test_postgres_boolean_data_preflight_reports_aggregate_counts_only():
    from db_schema_audit import build_postgres_data_preflight

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE telegram_users (
            id INTEGER PRIMARY KEY,
            is_premium INTEGER
        );
        INSERT INTO telegram_users VALUES (1, 0);
        INSERT INTO telegram_users VALUES (2, 1);
        INSERT INTO telegram_users VALUES (3, 2);
        INSERT INTO telegram_users VALUES (4, NULL);
        """
    )
    report = build_postgres_data_preflight(connection)
    connection.close()

    assert report["profiled_column_count"] == 1
    assert report["unsafe_column_count"] == 1
    assert report["columns"] == [{
        "table": "telegram_users",
        "column": "is_premium",
        "total_rows": 4,
        "invalid_value_count": 1,
        "null_value_count": 1,
        "boolean_conversion_safe": False,
    }]
    rendered = json.dumps(report, ensure_ascii=False)
    assert '"2"' not in rendered
    assert report["ready_for_cutover"] is False


def test_postgres_boolean_data_preflight_accepts_only_zero_one_non_null_values():
    from db_schema_audit import build_postgres_data_preflight

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE telegram_business_connections (
            is_enabled INTEGER NOT NULL
        );
        INSERT INTO telegram_business_connections VALUES (0);
        INSERT INTO telegram_business_connections VALUES (1);
        """
    )
    report = build_postgres_data_preflight(connection)
    connection.close()

    assert report["safe_column_count"] == 1
    assert report["unsafe_column_count"] == 0
    assert report["columns"][0]["boolean_conversion_safe"] is True
