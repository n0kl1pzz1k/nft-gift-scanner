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
    "telegram_users", "user_profiles", "telegram_sessions", "telegram_initdata_uses",
    "telegram_update_inbox", "user_favorites", "demo_orders", "demo_order_events",
    "demo_order_revisions", "demo_order_reports", "api_rate_limits", "app_settings",
    "demo_offers", "demo_deals", "demo_deal_events", "demo_deal_lifecycle_events",
    "demo_notifications", "catalogue_gifts", "catalogue_gift_snapshots",
)
OPTIONAL_SOURCE_TABLES = {"catalogue_gifts", "catalogue_gift_snapshots"}
SEQUENCE_COLUMNS = {
    "demo_order_events": "id",
    "demo_order_revisions": "id",
    "demo_deal_events": "sequence",
    "demo_deal_lifecycle_events": "sequence",
}


def _hash_rows(rows, columns: list[str]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        value = {name: row[name] for name in columns}
        digest.update(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _source_batches(source, table: str, columns: list[str], order_columns: list[str], batch_size: int):
    selected = ", ".join('"' + name + '"' for name in columns)
    ordered = ", ".join('"' + name + '"' for name in order_columns)
    cursor = source.execute('SELECT ' + selected + ' FROM "' + table + '" ORDER BY ' + ordered)
    while True:
        batch = cursor.fetchmany(batch_size)
        if not batch:
            break
        yield [{name: row[name] for name in columns} for row in batch]


def migrate(source_path: Path, apply: bool, source_quiesced: bool = False, batch_size: int = 500) -> dict[str, Any]:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required; its value is never printed")
    if apply and not source_quiesced:
        raise RuntimeError("Apply requires --source-quiesced after the marketplace is in maintenance mode")
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite source database not found: {source_path}")
    if not 1 <= batch_size <= 5000:
        raise ValueError("batch size must be between 1 and 5000")

    source = sqlite3.connect(f"file:{source_path.resolve()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only = ON")
    source.execute("BEGIN")  # pin a consistent read snapshot for the entire export
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
                raise RuntimeError("Target schema missing tables: " + ", ".join(missing_targets))

            source_tables = {
                row["name"] for row in source.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            missing_required = sorted((set(TABLES) - OPTIONAL_SOURCE_TABLES) - source_tables)
            if missing_required:
                raise RuntimeError("SQLite source missing required tables: " + ", ".join(missing_required))

            target_counts = {
                name: connection.execute(text('SELECT COUNT(*) FROM "' + name + '"')).scalar_one()
                for name in TABLES
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
        primary_keys: dict[str, list[str]] = {}

        for name in TABLES:
            target_table = metadata.tables[name]
            if name not in source_tables:
                report["tables"][name] = {"rows": 0, "source_table_absent": True, "verified": True}
                source_counts[name] = 0
                source_hashes[name] = _hash_rows([], [])
                column_maps[name] = []
                primary_keys[name] = []
                continue

            source_info = source.execute('PRAGMA table_info("' + name + '")').fetchall()
            source_columns = [row["name"] for row in source_info]
            columns = [column.name for column in target_table.columns if column.name in source_columns]
            keys = [column.name for column in target_table.primary_key.columns if column.name in columns]
            if not columns or not keys:
                raise RuntimeError("No compatible columns or shared primary key found for " + name)

            count = source.execute('SELECT COUNT(*) FROM "' + name + '"').fetchone()[0]
            digest = _hash_rows(
                (row for batch in _source_batches(source, name, columns, keys, batch_size) for row in batch),
                columns,
            )
            source_counts[name] = count
            source_hashes[name] = digest
            column_maps[name] = columns
            primary_keys[name] = keys
            report["tables"][name] = {"rows": count, "columns": columns, "sha256": digest}

        if not apply:
            report["verified_source_tables"] = len(TABLES)
            report["target_is_empty"] = True
            return report

        with engine.begin() as connection:
            connection.execute(text("SET LOCAL gift_radar.migrating = 'on'"))
            for name in TABLES:
                columns = column_maps[name]
                if not columns:
                    continue
                table = metadata.tables[name]
                for batch in _source_batches(source, name, columns, primary_keys[name], batch_size):
                    if batch:
                        connection.execute(table.insert(), batch)

            for name, column in SEQUENCE_COLUMNS.items():
                if name not in source_tables:
                    continue
                statement = (
                    "SELECT setval(pg_get_serial_sequence(:table_name, :column_name), "
                    "GREATEST(COALESCE(MAX(" + column + "), 1), 1), "
                    "MAX(" + column + ") IS NOT NULL) FROM " + name
                )
                connection.execute(text(statement), {"table_name": name, "column_name": column})

            for name in TABLES:
                if not column_maps[name]:
                    continue
                columns = column_maps[name]
                count = connection.execute(text('SELECT COUNT(*) FROM "' + name + '"')).scalar_one()
                if count != source_counts[name]:
                    raise RuntimeError(f"Row-count mismatch for {name}: {count} != {source_counts[name]}")
                selected = ", ".join('"' + column + '"' for column in columns)
                ordered = ", ".join('"' + column + '"' for column in primary_keys[name])
                rows = connection.execute(text(
                    'SELECT ' + selected + ' FROM "' + name + '" ORDER BY ' + ordered
                )).mappings()
                if _hash_rows(rows, columns) != source_hashes[name]:
                    raise RuntimeError("Content checksum mismatch after import for " + name)
                report["tables"][name]["verified"] = True

        report["verified_source_tables"] = len(TABLES)
        report["committed"] = True
        return report
    finally:
        source.close()
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate GIFT RADAR SQLite marketplace data to PostgreSQL")
    parser.add_argument("--source", default=os.getenv("GIFT_RADAR_SQLITE_PATH", "./data/gift-radar.sqlite3"))
    parser.add_argument("--apply", action="store_true", help="commit migration; without this flag only validate")
    parser.add_argument("--source-quiesced", action="store_true", help="confirm all SQLite writers are stopped for the cutover window")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    result = migrate(Path(args.source), args.apply, args.source_quiesced, args.batch_size)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
