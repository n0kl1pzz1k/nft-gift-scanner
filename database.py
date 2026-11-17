"""Central persistence connection boundary.

The current production adapter is SQLite. Keep connection policy in one module
so a future PostgreSQL adapter can replace it without changing marketplace
domain code. This module deliberately does not inspect DATABASE_URL or switch
engines implicitly.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

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
