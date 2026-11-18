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

    if configured_backend not in {"", "sqlite", "postgres"}:
        raise RuntimeError(
            "The configured HASSLE_NFT_DB_BACKEND is not supported by this application build."
        )
    if configured_backend == "postgres":
        dsn = database_url or values.get("HASSLE_POSTGRES_DSN", "").strip()
        if not dsn or not dsn.startswith(("postgresql://", "postgres://")):
            raise RuntimeError(
                "PostgreSQL backend requires DATABASE_URL or HASSLE_POSTGRES_DSN "
                "with a PostgreSQL connection URL."
            )
        return "postgres"
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



def is_integrity_error(error: BaseException) -> bool:
    """Recognize integrity violations from SQLite and psycopg without masking other errors."""
    if isinstance(error, sqlite3.IntegrityError):
        return True
    return any(
        cls.__name__ == "IntegrityError" and cls.__module__.startswith("psycopg")
        for cls in type(error).__mro__
    )


def list_database_tables(connection: object) -> set[str]:
    """List base tables using the active backend's system catalog."""
    if isinstance(connection, sqlite3.Connection):
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return {str(row["name"]) for row in rows}
    if bool(getattr(connection, "supports_for_update", False)):
        rows = connection.execute(
            "SELECT table_name AS name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
        ).fetchall()
        return {str(row["name"]) for row in rows}
    raise RuntimeError("Unsupported database connection for schema introspection")


def list_database_columns(connection: object, table_name: str) -> set[str]:
    """List columns for one table without exposing backend-specific SQL to callers."""
    if not isinstance(table_name, str) or not table_name:
        raise ValueError("Table name must be a non-empty string")
    if isinstance(connection, sqlite3.Connection):
        quoted_table = '"' + table_name.replace('"', '""') + '"'
        rows = connection.execute(f"PRAGMA table_info({quoted_table})").fetchall()
        return {str(row["name"]) for row in rows}
    if bool(getattr(connection, "supports_for_update", False)):
        rows = connection.execute(
            "SELECT column_name AS name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ? "
            "ORDER BY ordinal_position",
            (table_name,),
        ).fetchall()
        return {str(row["name"]) for row in rows}
    raise RuntimeError("Unsupported database connection for schema introspection")


def require_sqlite_connection(connection: object, operation: str) -> sqlite3.Connection:
    """Reject SQLite-only schema/runtime operations before issuing any SQL."""
    if not isinstance(connection, sqlite3.Connection):
        raise RuntimeError(
            f"{operation} requires SQLite; PostgreSQL needs its reviewed migration path."
        )
    return connection


def connect_postgres_database(dsn: str):
    """Open a staged PostgreSQL connection without changing the production default.

    This explicit factory is for isolated integration tests and reviewed
    migration tooling only. The application connection factory remains
    SQLite until all runtime compatibility blockers have been resolved.
    """
    if not isinstance(dsn, str) or not dsn.strip():
        raise ValueError("A non-empty PostgreSQL DSN is required")

    try:
        import psycopg
    except ImportError as error:
        raise RuntimeError("PostgreSQL support requires the psycopg package") from error

    from db_postgres_compat import PostgresConnectionAdapter

    connection = psycopg.connect(dsn.strip(), autocommit=True)
    try:
        return PostgresConnectionAdapter(connection)
    except BaseException:
        connection.close()
        raise


def connect(database_path: str | Path | None = None):
    """Open the shared application connection boundary.

    SQLite keeps its path override. When PostgreSQL is explicitly selected,
    the configured DSN is authoritative and the legacy SQLite path is ignored.
    This switch is opt-in; production remains on SQLite unless the backend
    environment variable is deliberately changed.
    """
    backend = validate_database_configuration()
    if backend == "postgres":
        dsn = os.getenv("DATABASE_URL", "").strip() or os.getenv("HASSLE_POSTGRES_DSN", "").strip()
        return connect_postgres_database(dsn)
    return connect_database(DB_PATH if database_path is None else database_path)


@contextmanager
def db(database_path: str | Path | None = None) -> Iterator[object]:
    """Yield one connection and always close it.

    This context manager does not begin, commit, or roll back transactions.
    Callers retain the existing explicit transaction/autocommit semantics.
    """
    connection = connect() if database_path is None else connect(database_path)
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
