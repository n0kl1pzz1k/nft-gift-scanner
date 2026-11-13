"""One-way, auditable SQLite-to-PostgreSQL marketplace data migration.

Default mode is dry-run. The script refuses to overwrite a non-empty target.
It never prints DATABASE_URL or any credential.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, create_engine, text


TABLES = (
    "telegram_users",
    "user_profiles",
    "telegram_sessions",
    "telegram_initdata_uses",
    "telegram_update_inbox",
    "user_favorites",
    "demo_orders",
    "demo_order_events",
    "demo_order_revisions",
    "demo_order_reports",
    "api_rate_limits",
    "app_settings",
    "demo_offers",
    "demo_deals",
    "demo_deal_events",
    "demo_deal_lifecycle_events",
    "demo_notifications",
    "catalogue_gifts",
    "catalogue_gift_snapshots",
)
SEQUENCE_COLUMNS = {
    "demo_order_events": "id",
    "demo_order_revisions": "id",
    "demo_deal_events": "sequence",
    "demo_deal_lifecycle_events": "sequence",
}


def _canonical_hash(rows, columns: list[str]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        item = {column: row[column] for column in columns}
        digest.update(json.dumps(item, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _source_rows(source: sqlite3.Connection, table: str, columns: list[str], batch_size: int):
    order = ", ".join(f'"{column}"' for column in columns)
    cursor = source.execute(f'SELECT {", ".join(f""" "{column}" """ for column in columns)} FROM "{table}" ORDER BY {order}')
    while True:
        batch = cursor.fetchmany(batch_size)
        if not batch:
            break
        yield [{column: row[column] for column in columns} for row in batch]


def migrate(source_path: Path, apply: bool, batch_size: int = 500) -> dict[str, Any]:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required; its value is never printed")
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite source database not found: {source_path}")
    if batch_size < 1 or batch_size > 5000:
        raise ValueError("batch size must be between 1 and 5000")

    source = sqlite3.connect(f"file:{source_path.resolve()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only = ON")
    engine = create_engine(database_url, pool_pre_ping=True, future=True)
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
            if revision != "0002_marketplace_core":
                raise RuntimeError("Target must be migrated to Alembic revision 0002_marketplace_core")
            metadata = MetaData()
            metadata.reflect(bind=connection, only=list(TABLES))
            missing_targets = sorted(set(TABLES) - set(metadata.tables))
            if missing_targets:
                raise RuntimeError(f"Target schema is missing tables: {', '.join(missing_targets)}")
            source_tables = {
                row["name"] for row in source.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            missing_sources = sorted(set(TABLES) - source_tables)
            if missing_sources:
                raise RuntimeError(f"SQLite source is missing required tables: {', '.join(missing_sources)}")
            target_counts = {
                table: connection.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()
                for table in TABLES
            }
            occupied = {name: count for name, count in target_counts.items() if count}
            if occupied:
                raise RuntimeError(
                    "Refusing to overwrite non-empty PostgreSQL tables: "
                    + ", ".join(f"{name}={count}" for name, count in occupied.items())
                )

        report: dict[str, Any] = {"mode": "apply" if apply else "dry-run", "tables": {}}
        source_counts: dict[str, int] = {}
        source_hashes: dict[str, str] = {}
        column_maps: dict[str, list[str]] = {}
        for table_name in TABLES:
            target_table = metadata.tables[table_name]
            source_columns = [row["name"] for row in source.execute(f'PRAGMA table_info("{table_name}")')]
            columns = [column.name for column in target_table.columns if column.name in source_columns]
            if not columns:
                raise RuntimeError(f"No compatible columns found for {table_name}")
            primary_keys = [column.name for column in target_table.primary_key.columns if column.name in columns]
            if not primary_keys:
                raise RuntimeError(f"No shared primary key found for {table_name}")
            count = source.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            hash_value = _canonical_hash(
                (row for batch in _source_rows(source, table_name, columns, batch_size) for row in batch),
                columns,
            )
            source_counts[table_name] = count
            source_hashes[table_name] = hash_value
            column_maps[table_name] = columns
            report["tables"][table_name] = {"rows": count, "columns": columns, "sha256": hash_value}

        if not apply:
            report["verified_source_tables"] = len(TABLES)
            report["target_is_empty"] = True
            return report

        with engine.begin() as connection:
            connection.execute(text("SET LOCAL gift_radar.migrating = 'on'"))
            for table_name in TABLES:
                target_table = metadata.tables[table_name]
                columns = column_maps[table_name]
                for batch in _source_rows(source, table_name, columns, batch_size):
                    if batch:
                        connection.execute(target_table.insert(), batch)

            for table_name, column_name in SEQUENCE_COLUMNS.items():
                connection.execute(text(
                    "SELECT setval(pg_get_serial_sequence(:table_name, :column_name), "
                    "GREATEST(COALESCE(MAX(" + column_name + "), 1), 1), "
                    "MAX(" + column_name + ") IS NOT NULL) FROM " + table_name
                ), {"table_name": table_name, "column_name": column_name})

            for table_name in TABLES:
                target_table = metadata.tables[table_name]
                columns = column_maps[table_name]
                count = connection.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar_one()
                if count != source_counts[table_name]:
                    raise RuntimeError(f"Row-count mismatch after import for {table_name}: {count} != {source_counts[table_name]}")
                order_columns = [column.name for column in target_table.primary_key.columns if column.name in columns]
                order_sql = ", ".join(f'"{column}"' for column in order_columns)
                result = connection.execute(text(
                    f'SELECT {", ".join(f""" "{column}" """ for column in columns)} '
                    f'FROM "{table_name}" ORDER BY {order_sql}'
                ))
                target_hash = _canonical_hash(result.mappings(), columns)
                if target_hash != source_hashes[table_name]:
                    raise RuntimeError(f"Content checksum mismatch after import for {table_name}")
                report["tables"][table_name]["verified"] = True

        report["verified_source_tables"] = len(TABLES)
        report["committed"] = True
        return report
    finally:
        source.close()
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate GIFT RADAR SQLite marketplace data to PostgreSQL")
    parser.add_argument("--source", default=os.getenv("GIFT_RADAR_SQLITE_PATH", "./data/gift-radar.sqlite3"))
    parser.add_argument("--apply", action="store_true", help="commit the migration; without this flag only validate")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    report = migrate(Path(args.source), args.apply, args.batch_size)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
