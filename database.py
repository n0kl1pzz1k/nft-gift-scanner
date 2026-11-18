"""Central persistence connection boundary.

The current production adapter is SQLite. Keep connection policy in one module
so a future PostgreSQL adapter can replace it without changing marketplace
domain code. Unsupported backend configuration must fail closed rather than
silently writing to SQLite while an operator expects PostgreSQL.
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def validate_database_configuration(environ: dict[str, str] | None = None) -> str:
    """Fail closed when configuration implies an unsupported PostgreSQL backend."""
    values = os.environ if environ is None else environ
    configured_backend = values.get("HASSLE_NFT_DB_BACKEND", "").strip().lower()
    database_url = values.get("DATABASE_URL", "").strip()

    if configured_backend and configured_backend != "sqlite":
        raise RuntimeError(
            "The configured HASSLE_NFT_DB_BACKEND is not supported by this application build."
        )
    if database_url and not configured_backend:
        raise RuntimeError(
            "DATABASE_URL is configured but PostgreSQL runtime support is not enabled; "
            "set HASSLE_NFT_DB_BACKEND=sqlite to explicitly keep SQLite active."
        )
    return "sqlite"


validate_database_configuration()

DB_PATH = os.getenv(
    "HASSLE_NFT_DB_PATH",
    os.getenv("GIFT_RADAR_DB_PATH", os.getenv("DATABASE_PATH", "./data/gift-radar.sqlite3")),
)


def connect_database(database_path: str | Path) -> sqlite3.Connection:
    """Open SQLite with the existing durability and contention policy.

    SQLite remains a single-writer, single-instance deployment baseline.
    These settings reduce transient lock failures but do not replace PostgreSQL.
    """
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA temp_store = MEMORY")
    return connection


def connect() -> sqlite3.Connection:
    """Open the configured application database."""
    return connect_database(DB_PATH)


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    """Yield one connection and always close it.

    This context manager does not begin, commit, or roll back transactions.
    Callers retain the existing explicit transaction/autocommit semantics.
    """
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()



def begin_write(connection: object, *lock_keys: str) -> None:
    """Begin a write transaction with backend-specific serialization.

    SQLite keeps its existing BEGIN IMMEDIATE single-writer behavior.
    PostgreSQL uses transaction-scoped advisory locks for the supplied stable
    business keys. Callers must still lock the actual rows they read/modify
    with SELECT ... FOR UPDATE where applicable.
    """
    if isinstance(connection, sqlite3.Connection):
        connection.execute("BEGIN IMMEDIATE")
        return
    begin = getattr(connection, "begin_write", None)
    if callable(begin):
        begin(*lock_keys)
        return
    raise RuntimeError("Unsupported database connection for write transaction")


def select_for_update(connection: object, statement: str) -> str:
    """Append PostgreSQL row locking while leaving SQLite SQL unchanged."""
    if bool(getattr(connection, "supports_for_update", False)):
        sql = statement.rstrip().rstrip(";")
        if re.search(r"\bFOR\s+UPDATE\b", sql, re.IGNORECASE):
            return sql
        return sql + " FOR UPDATE"
    return statement
