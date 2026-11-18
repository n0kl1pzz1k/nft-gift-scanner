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
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        payload = json.dumps([_normal(value) for value in row], ensure_ascii=False, separators=(",", ":"))
        digest.update(payload.encode("utf-8"))
        digest.update(b"\n")
        count += 1
    return count, digest.hexdigest()


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


def _pg_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def migrate(source_path: str, dsn: str, batch_size: int, execute: bool) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve(strict=True)
    source_uri = f"file:{source.as_posix()}?mode=ro"
    sqlite = sqlite3.connect(source_uri, uri=True)
    sqlite.row_factory = sqlite3.Row
    try:
        integrity = str(sqlite.execute("PRAGMA integrity_check").fetchone()[0])
        fk_errors = int(sqlite.execute("SELECT COUNT(*) FROM pragma_foreign_key_check").fetchone()[0])
        if integrity != "ok" or fk_errors:
            raise RuntimeError(f"Source validation failed: integrity={integrity}, foreign_key_errors={fk_errors}")
        manifest = build_schema_manifest(sqlite)
        tables = sorted(manifest["tables"], key=lambda item: item["name"])
        import psycopg
        from psycopg import sql

        report: dict[str, Any] = {
            "source_schema_sha256": manifest["schema_sha256"],
            "table_count": len(tables),
            "batch_size": batch_size,
            "execute": execute,
            "tables": [],
            "committed": False,
        }
        with psycopg.connect(dsn, autocommit=False) as pg:
            with pg.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL DEFERRED")
                target_tables = {
                    row[0] for row in cursor.execute(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() AND table_type='BASE TABLE'"
                    ).fetchall()
                }
                source_names = {str(item["name"]) for item in tables}
                missing = sorted(source_names - target_tables)
                if missing:
                    raise RuntimeError(f"Target is missing tables: {missing}")
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
                                if max_id is not None:
                                    cursor.execute("SELECT setval(%s, %s, true)", (sequence, max_id))
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
