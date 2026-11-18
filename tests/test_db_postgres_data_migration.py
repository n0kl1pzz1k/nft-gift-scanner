import sqlite3

from db_postgres_data_migration import _fingerprint, _normal, _ordered_select, _sqlite_autoincrement_high_water
from db_schema_audit import build_schema_manifest


def test_fingerprint_normalizes_sqlite_and_postgres_numeric_adapters():
    from decimal import Decimal
    assert _normal(1) == _normal(True)
    assert _normal(1.25) == _normal(Decimal("1.250"))
    assert _fingerprint([(1, "x"), (2, None)])[0] == 2


def test_ordered_select_uses_primary_key_order_and_quoted_identifiers():
    connection = sqlite3.connect(":memory:")
    connection.execute('CREATE TABLE "odd table" ("key id" TEXT PRIMARY KEY, value TEXT)')
    manifest = build_schema_manifest(connection)
    statement, columns = _ordered_select(connection, manifest["tables"][0])
    assert columns == ["key id", "value"]
    assert 'ORDER BY "key id"' in statement
    connection.close()


def test_ordered_select_falls_back_to_all_columns_without_primary_key():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE no_key (a TEXT, b INTEGER)")
    manifest = build_schema_manifest(connection)
    statement, columns = _ordered_select(connection, manifest["tables"][0])
    assert 'ORDER BY "a", "b"' in statement
    connection.close()



def test_fingerprint_is_independent_of_row_order():
    rows = [(1, "alpha"), (2, "beta"), (3, None)]
    assert _fingerprint(rows) == _fingerprint(list(reversed(rows)))



def test_autoincrement_high_water_survives_deleted_highest_row():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT)")
    connection.execute("INSERT INTO items(value) VALUES ('first')")
    connection.execute("INSERT INTO items(value) VALUES ('second')")
    connection.execute("DELETE FROM items WHERE id = 2")
    assert _sqlite_autoincrement_high_water(connection, "items") == 2
    connection.close()
