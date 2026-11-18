"""Explicit, transactional SQLite-to-PostgreSQL data copy runner.

The target schema must already exist. This command defaults to a dry run,
requires --execute to write, refuses non-empty target tables, and performs
all table copies in one PostgreSQL transaction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from db_schema_audit import _quote_identifier, build_schema_manifest
from db_backup import _row_counts, _schema_metadata, _sequence_counters, _sha256_file


def _normal(value: Any) -> Any:
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["number", str(int(value))]
    if isinstance(value, int):
        return ["number", str(value)]
    if isinstance(value, (float, Decimal)):
        return ["number", format(Decimal(str(value)).normalize(), "f")]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return ["bytes", bytes(value).hex()]
    return ["text", str(value)]


def _fingerprint(rows: Iterable[tuple[Any, ...]]) -> tuple[int, str]:
    # Commutative aggregation makes the fingerprint independent of row order,
    # which can differ between SQLite and PostgreSQL collations/NULL ordering.
    count = 0
    total = 0
    xor = 0
    modulus = 1 << 256
    for row in rows:
        payload = json.dumps([_normal(value) for value in row], ensure_ascii=False, separators=(",", ":"))
        row_hash = int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest(), "big")
        total = (total + row_hash) % modulus
        xor ^= row_hash
        count += 1
    aggregate = f"{count}:{total:064x}:{xor:064x}".encode("ascii")
    return count, hashlib.sha256(aggregate).hexdigest()


def _ordered_select(connection: sqlite3.Connection, table: dict[str, Any]) -> tuple[str, list[str]]:
    name = str(table["name"])
    columns = [str(item["name"]) for item in table["columns"] if int(item.get("hidden", 0)) == 0]
    if not columns:
        raise RuntimeError(f"Table {name} has no migratable visible columns")
    primary = [
        str(item["name"]) for item in sorted(
            (col for col in table["columns"] if int(col.get("primary_key_position", 0)) > 0),
            key=lambda col: int(col["primary_key_position"]),
        )
    ]
    order = primary or columns
    sql = f"SELECT {', '.join(_quote_identifier(col) for col in columns)} FROM {_quote_identifier(name)} ORDER BY {', '.join(_quote_identifier(col) for col in order)}"
    return sql, columns



def _sqlite_autoincrement_high_water(connection: sqlite3.Connection, table_name: str) -> int:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).fetchone()
    if not exists:
        return 0
    row = connection.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = ?", (table_name,)
    ).fetchone()
    return max(0, int(row[0])) if row else 0

def _pg_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _validate_target_schema(cursor: Any, tables: list[dict[str, Any]]) -> set[str]:
    """Require an exact target table/column layout before any copy is attempted."""
    source_names = {str(item["name"]) for item in tables}
    target_names = {
        str(row[0]) for row in cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
        ).fetchall()
    }
    missing = sorted(source_names - target_names)
    extra = sorted(target_names - source_names)
    if missing or extra:
        raise RuntimeError(
            f"Target table set does not exactly match the verified SQLite backup: "
            f"missing={missing}, extra={extra}"
        )

    for table in tables:
        name = str(table["name"])
        expected = {
            str(column["name"]) for column in table["columns"]
            if int(column.get("hidden", 0)) == 0
        }
        actual = {
            str(row[0]) for row in cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = %s",
                (name,),
            ).fetchall()
        }
        if actual != expected:
            raise RuntimeError(
                f"Target columns for {name} do not exactly match the verified SQLite backup: "
                f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
            )
    return target_names


def migrate(source_path: str, dsn: str, batch_size: int, execute: bool) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve(strict=True)
    source_uri = f"file:{source.as_posix()}?mode=ro"
    sqlite = sqlite3.connect(source_uri, uri=True)
    sqlite.row_factory = sqlite3.Row
    try:
        sqlite.execute("PRAGMA query_only = ON")
        sqlite.execute("BEGIN")
        integrity = str(sqlite.execute("PRAGMA integrity_check").fetchone()[0])
        fk_errors = int(sqlite.execute("SELECT COUNT(*) FROM pragma_foreign_key_check").fetchone()[0])
        if integrity != "ok" or fk_errors:
            raise RuntimeError(f"Source validation failed: integrity={integrity}, foreign_key_errors={fk_errors}")

        sidecar = Path(str(source) + ".manifest.json")
        if not sidecar.is_file():
            raise RuntimeError("Refusing migration: verified SQLite backup sidecar manifest is missing.")
        try:
            backup_manifest = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Refusing migration: backup sidecar manifest is unreadable.") from exc
        if backup_manifest.get("format_version") != 1 or backup_manifest.get("backup_file") != source.name:
            raise RuntimeError("Refusing migration: backup sidecar identity or format does not match.")
        if backup_manifest.get("backup_sha256") != _sha256_file(source):
            raise RuntimeError("Refusing migration: backup file SHA256 does not match its sidecar manifest.")
        _, schema_sha256 = _schema_metadata(sqlite)
        row_counts = _row_counts(sqlite)
        sequence_counters = _sequence_counters(sqlite)
        if (
            backup_manifest.get("integrity_check") != "ok"
            or int(backup_manifest.get("foreign_key_violations", -1)) != 0
            or backup_manifest.get("schema_sha256") != schema_sha256
            or backup_manifest.get("tables") != row_counts
            or backup_manifest.get("sequence_counters", {}) != sequence_counters
            or int(backup_manifest.get("table_count", -1)) != len(row_counts)
            or int(backup_manifest.get("total_rows", -1)) != sum(row_counts.values())
        ):
            raise RuntimeError("Refusing migration: backup contents do not match verified sidecar metadata.")

        manifest = build_schema_manifest(sqlite)
        tables = sorted(manifest["tables"], key=lambda item: item["name"])
        import psycopg
        from psycopg import sql

        report: dict[str, Any] = {
            "source_schema_sha256": manifest["schema_sha256"],
            "source_backup_sha256": backup_manifest["backup_sha256"],
            "source_backup_manifest_verified": True,
            "table_count": len(tables),
            "batch_size": batch_size,
            "execute": execute,
            "tables": [],
            "committed": False,
        }
        with psycopg.connect(dsn, autocommit=False) as pg:
            with pg.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL DEFERRED")
                _validate_target_schema(cursor, tables)
                for table in tables:
                    name = str(table["name"])
                    select_sql, columns = _ordered_select(sqlite, table)
                    qname = _pg_identifier(name)
                    qcols = ", ".join(_pg_identifier(col) for col in columns)
                    target_count = cursor.execute(
                        sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(name))
                    ).fetchone()[0]
                    if int(target_count) != 0:
                        raise RuntimeError(f"Target table {name} is not empty; refusing to overwrite or merge.")
                    source_count, source_sha256 = _fingerprint(sqlite.execute(select_sql))
                    report["tables"].append({
                        "table": name, "source_rows": source_count,
                        "source_sha256": source_sha256,
                        "target_rows_before": int(target_count), "columns": len(columns),
                    })
                if execute:
                    # Do not replay business side effects for historical rows.
                    # DISABLE TRIGGER USER leaves internal FK enforcement active.
                    for table in tables:
                        cursor.execute(
                            sql.SQL("ALTER TABLE {} DISABLE TRIGGER USER").format(
                                sql.Identifier(str(table["name"]))
                            )
                        )
                    for table in tables:
                        name = str(table["name"])
                        select_sql, columns = _ordered_select(sqlite, table)
                        copy_stmt = f"COPY {_pg_identifier(name)} ({', '.join(_pg_identifier(col) for col in columns)}) FROM STDIN"
                        source_cursor = sqlite.execute(select_sql)
                        copied = 0
                        with pg.cursor().copy(copy_stmt) as copy:
                            while True:
                                batch = source_cursor.fetchmany(batch_size)
                                if not batch:
                                    break
                                for row in batch:
                                    copy.write_row(tuple(row))
                                copied += len(batch)
                        expected = next(item["source_rows"] for item in report["tables"] if item["table"] == name)
                        if copied != expected:
                            raise RuntimeError(f"Copied row count mismatch for {name}: {copied} != {expected}")
                    for table in tables:
                        cursor.execute(
                            sql.SQL("ALTER TABLE {} ENABLE TRIGGER USER").format(
                                sql.Identifier(str(table["name"]))
                            )
                        )
                    for table in tables:
                        name = str(table["name"])
                        pk = [
                            str(item["name"]) for item in sorted(
                                (col for col in table["columns"] if int(col.get("primary_key_position", 0)) > 0),
                                key=lambda col: int(col["primary_key_position"]),
                            )
                        ]
                        if len(pk) == 1 and str(next(col for col in table["columns"] if col["name"] == pk[0])["type"]).upper() == "INTEGER" and "AUTOINCREMENT" in str(table.get("sql") or "").upper():
                            sequence = cursor.execute("SELECT pg_get_serial_sequence(%s, %s)", (name, pk[0])).fetchone()[0]
                            if sequence:
                                max_id = cursor.execute(sql.SQL("SELECT MAX({}) FROM {}").format(sql.Identifier(pk[0]), sql.Identifier(name))).fetchone()[0]
                                sqlite_high_water = _sqlite_autoincrement_high_water(sqlite, name)
                                high_water = max(sqlite_high_water, int(max_id or 0))
                                if high_water > 0:
                                    cursor.execute("SELECT setval(%s, %s, true)", (sequence, high_water))
                                else:
                                    cursor.execute("SELECT setval(%s, 1, false)", (sequence,))
                    for table, item in zip(tables, report["tables"]):
                        name = str(table["name"])
                        select_sql, columns = _ordered_select(sqlite, table)
                        target_order = [
                            str(col["name"]) for col in sorted(
                                (col for col in table["columns"] if int(col.get("primary_key_position", 0)) > 0),
                                key=lambda col: int(col["primary_key_position"]),
                            )
                        ] or columns
                        target_select = sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
                            sql.SQL(", ").join(sql.Identifier(col) for col in columns),
                            sql.Identifier(name),
                            sql.SQL(", ").join(sql.Identifier(col) for col in target_order),
                        )
                        target_cursor = cursor.execute(target_select)
                        def target_rows():
                            while True:
                                batch = target_cursor.fetchmany(batch_size)
                                if not batch:
                                    break
                                yield from batch
                        target_count, target_sha256 = _fingerprint(target_rows())
                        if target_count != item["source_rows"] or target_sha256 != item["source_sha256"]:
                            raise RuntimeError(
                                f"Post-copy content fingerprint mismatch for {name}: "
                                f"rows {target_count}/{item['source_rows']}, "
                                f"sha256 {target_sha256}/{item['source_sha256']}"
                            )
                        item["target_rows"] = target_count
                        item["target_sha256"] = target_sha256
                        item["content_verified"] = True
                    pg.commit()
                    report["committed"] = True
                else:
                    pg.rollback()
        return report
    finally:
        sqlite.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy SQLite rows into a pre-created empty PostgreSQL schema.")
    parser.add_argument("--source", required=True, help="Verified SQLite backup path")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--execute", action="store_true", help="Perform the copy; otherwise perform validation only")
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 10000:
        parser.error("--batch-size must be between 1 and 10000")
    dsn = os.getenv("HASSLE_POSTGRES_MIGRATION_DSN")
    if not dsn:
        parser.error("Set HASSLE_POSTGRES_MIGRATION_DSN in the environment; its value is never printed.")
    result = migrate(args.source, dsn, args.batch_size, args.execute)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
