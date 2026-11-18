import json
import sqlite3

from db_postgres_migration_plan import build_postgres_migration_plan
from db_schema_audit import build_schema_manifest


def test_postgres_plan_orders_parent_tables_before_children_and_preserves_constraints():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE parent (id TEXT PRIMARY KEY);
        CREATE TABLE child (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('open', 'closed')),
            FOREIGN KEY(parent_id) REFERENCES parent(id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX idx_child_open ON child(parent_id) WHERE state = 'open';
        CREATE TRIGGER child_no_delete BEFORE DELETE ON child
        BEGIN SELECT RAISE(ABORT, 'immutable'); END;
        """
    )
    plan = build_postgres_migration_plan(build_schema_manifest(connection))
    connection.close()

    assert plan["table_creation_order"] == ["parent", "child"]
    child = next(item for item in plan["tables"] if item["table"] == "child")
    assert child["primary_key"] == ["id"]
    assert child["has_autoincrement"] is True
    assert plan["implicit_rowid_primary_key_index_count"] == 1
    assert plan["index_count"] == plan["sqlite_index_count"] + 1
    assert child["foreign_keys"][0]["referenced_table"] == "parent"
    assert child["check_constraints"] == ["state IN ('open', 'closed')"]
    assert child["indexes"][0]["partial"] is True
    assert plan["trigger_rewrite_count"] == 1
    assert plan["ready_for_cutover"] is False
    assert plan["execution_enabled"] is False


def test_postgres_plan_defers_foreign_keys_inside_cycles():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE alpha (id TEXT PRIMARY KEY, beta_id TEXT,
            FOREIGN KEY(beta_id) REFERENCES beta(id));
        CREATE TABLE beta (id TEXT PRIMARY KEY, alpha_id TEXT,
            FOREIGN KEY(alpha_id) REFERENCES alpha(id));
        """
    )
    plan = build_postgres_migration_plan(build_schema_manifest(connection))
    connection.close()

    assert plan["cyclic_tables"] == ["alpha", "beta"]
    assert len(plan["deferred_foreign_keys"]) == 2
    assert set(plan["table_creation_order"]) == {"alpha", "beta"}


def test_postgres_plan_is_data_free():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE private_data (id TEXT PRIMARY KEY, note TEXT)")
    connection.execute("INSERT INTO private_data VALUES ('one', 'PRIVATE_ROW_SENTINEL')")
    plan = build_postgres_migration_plan(build_schema_manifest(connection))
    connection.close()

    assert "PRIVATE_ROW_SENTINEL" not in json.dumps(plan)
    assert plan["ready_for_cutover"] is False
