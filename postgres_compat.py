"""Small psycopg compatibility layer for the existing SQLite-oriented stores.

The adapter deliberately supports only the SQL subset used by GIFT RADAR.
It does not silently emulate SQLite DDL or PRAGMA operations.
"""
from __future__ import annotations

import os
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row


class HybridRow(dict):
    """Mapping row that also supports SQLite-style integer indexing."""

    def __init__(self, values: dict[str, Any]):
        super().__init__(values)
        self._values = tuple(values.values())

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class PostgresConnection:
    """Expose the tiny sqlite3.Connection surface used by the stores."""

    def __init__(self, raw: psycopg.Connection):
        self.raw = raw

    def execute(self, statement: str, parameters: Iterable[Any] | None = None):
        sql = statement.strip()
        if sql.upper() == "BEGIN IMMEDIATE":
            sql = "BEGIN"
        if sql.upper().startswith("PRAGMA"):
            raise RuntimeError("SQLite PRAGMA is not supported by PostgreSQL storage")
        sql = sql.replace("?", "%s")
        cursor = self.raw.execute(sql, tuple(parameters or ()))
        if cursor.description:
            names = [column.name for column in cursor.description]
            rows = cursor.fetchall()
            cursor.close()
            return _BufferedCursor(rows, names, cursor.rowcount)
        rowcount = cursor.rowcount
        cursor.close()
        return _BufferedCursor([], [], rowcount)

    def executescript(self, _script: str):
        raise RuntimeError("Runtime schema creation is disabled for PostgreSQL; apply Alembic migrations")

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        self.raw.close()


class _BufferedCursor:
    def __init__(self, rows: list[dict[str, Any]], names: list[str], rowcount: int):
        self._rows = [HybridRow({name: row[name] for name in names}) for row in rows]
        self._index = 0
        self.rowcount = rowcount

    def fetchone(self):
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self):
        rows = self._rows[self._index:]
        self._index = len(self._rows)
        return rows


def connect_postgres(database_url: str) -> PostgresConnection:
    if not database_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
        raise ValueError("DATABASE_URL must use PostgreSQL")
    normalized = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    if normalized.startswith("postgres://"):
        normalized = "postgresql://" + normalized[len("postgres://"):]
    raw = psycopg.connect(normalized, autocommit=True, row_factory=dict_row, connect_timeout=10)
    return PostgresConnection(raw)


def postgres_storage_enabled() -> bool:
    return os.getenv("GIFT_RADAR_STORAGE_BACKEND", "sqlite").strip().lower() == "postgres"


def assert_marketplace_schema_ready(connection: PostgresConnection) -> None:
    row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    if row is None or row[0] != "0002_marketplace_core":
        raise RuntimeError("PostgreSQL marketplace schema is not at Alembic revision 0002_marketplace_core")
