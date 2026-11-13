import unittest
from types import SimpleNamespace
from unittest.mock import patch

from postgres_compat import HybridRow, PostgresConnection, postgres_storage_enabled


class FakeCursor:
    def __init__(self, rows=None, description=None, rowcount=0):
        self._rows = rows or []
        self.description = description
        self.rowcount = rowcount
        self.closed = False

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True


class FakeRawConnection:
    def __init__(self):
        self.calls = []
        self.cursor = FakeCursor()

    def execute(self, sql, parameters):
        self.calls.append((sql, parameters))
        if sql.startswith("SELECT"):
            self.cursor = FakeCursor(
                [{"id": 7, "label": "test"}],
                [SimpleNamespace(name="id"), SimpleNamespace(name="label")],
                1,
            )
        else:
            self.cursor = FakeCursor([], None, 1)
        return self.cursor


class PostgresCompatTests(unittest.TestCase):
    def test_hybrid_row_supports_mapping_and_sqlite_integer_access(self):
        row = HybridRow({"id": 7, "label": "gift"})
        self.assertEqual(row["id"], 7)
        self.assertEqual(row[0], 7)
        self.assertEqual(row[1], "gift")
        self.assertEqual(dict(row), {"id": 7, "label": "gift"})

    def test_parameter_placeholders_are_translated(self):
        raw = FakeRawConnection()
        connection = PostgresConnection(raw)
        result = connection.execute("SELECT id FROM demo_orders WHERE id = ?", ("ord-1",))
        row = result.fetchone()
        self.assertEqual(raw.calls[0], ("SELECT id FROM demo_orders WHERE id = %s", ("ord-1",)))
        self.assertEqual(row[0], 7)
        self.assertEqual(row["label"], "test")

    def test_begin_immediate_is_mapped_to_transaction_begin(self):
        raw = FakeRawConnection()
        PostgresConnection(raw).execute("BEGIN IMMEDIATE")
        self.assertEqual(raw.calls[0][0], "BEGIN")

    def test_sqlite_schema_operations_fail_closed(self):
        raw = FakeRawConnection()
        connection = PostgresConnection(raw)
        with self.assertRaises(RuntimeError):
            connection.executescript("CREATE TABLE unsafe (id TEXT)")
        with self.assertRaises(RuntimeError):
            connection.execute("PRAGMA table_info(demo_orders)")

    def test_postgres_backend_is_opt_in(self):
        with patch.dict("os.environ", {"GIFT_RADAR_STORAGE_BACKEND": "postgres"}):
            self.assertTrue(postgres_storage_enabled())
        with patch.dict("os.environ", {"GIFT_RADAR_STORAGE_BACKEND": "sqlite"}):
            self.assertFalse(postgres_storage_enabled())


if __name__ == "__main__":
    unittest.main()
