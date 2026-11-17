"""Read-only verification that a SQLite backup exactly matches its source.

Reports contain schema metadata, row counts and opaque SHA-256 fingerprints only.
Application row values are never emitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from db_schema_audit import build_postgres_portability_report, build_schema_manifest
from db_sql_compat_audit import audit_source_tree


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _canonical(value: Any) -> Any:
    if value is None:
        return ["null", None]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    if isinstance(value, float):
        return ["float", value.hex()]
    if isinstance(value, int):
        return ["integer", value]
    if isinstance(value, str):
        return ["text", value]
    return [type(value).__name__, str(value)]


def _table_fingerprint(connection: sqlite3.Connection, table: dict[str, Any]) -> tuple[int, str]:
    columns = [c for c in table["columns"] if int(c.get("hidden", 0)) != 1]
    names = [str(c["name"]) for c in columns]
    primary_key = sorted(
        ((int(c["primary_key_position"]), str(c["name"])) for c in columns
         if int(c["primary_key_position"]) > 0),
        key=lambda item: item[0],
    )
    order = [name for _, name in primary_key] or sorted(names)
    query = f"SELECT * FROM {_quote(str(table['name']))}"
    if order:
        query += " ORDER BY " + ", ".join(_quote(name) for name in order)
    digest = hashlib.sha256()
    count = 0
    for row in connection.execute(query):
        encoded = json.dumps(
            [_canonical(value) for value in tuple(row)],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        count += 1
    return count, digest.hexdigest()


def _inspect(connection: sqlite3.Connection) -> dict[str, Any]:
    integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
    integrity = str(integrity_rows[0][0]) if len(integrity_rows) == 1 else "failed"
    fk_count = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    schema = build_schema_manifest(connection)
    counts: dict[str, int] = {}
    fingerprints: dict[str, str] = {}
    for table in schema["tables"]:
        count, fingerprint = _table_fingerprint(connection, table)
        name = str(table["name"])
        counts[name] = count
        fingerprints[name] = fingerprint
    try:
        sequences = {
            str(name): int(seq)
            for name, seq in connection.execute(
                "SELECT name, seq FROM sqlite_sequence ORDER BY name"
            ).fetchall()
        }
    except sqlite3.OperationalError:
        sequences = {}
    return {
        "integrity_check": integrity,
        "foreign_key_violations": fk_count,
        "schema_sha256": schema["schema_sha256"],
        "table_count": len(schema["tables"]),
        "total_rows": sum(counts.values()),
        "tables": counts,
        "table_sha256": fingerprints,
        "sequence_counters": sequences,
        "schema_manifest": schema,
    }


def build_migration_preflight(
    source: sqlite3.Connection,
    backup: sqlite3.Connection,
    repository_root: str | Path = ".",
) -> dict[str, Any]:
    """Compare source and backup contents without returning any row values."""
    left = _inspect(source)
    right = _inspect(backup)
    table_names_match = set(left["tables"]) == set(right["tables"])
    counts_match = left["tables"] == right["tables"]
    hashes_match = left["table_sha256"] == right["table_sha256"]
    sequences_match = left["sequence_counters"] == right["sequence_counters"]
    schema_match = left["schema_sha256"] == right["schema_sha256"]
    source_healthy = left["integrity_check"] == "ok" and left["foreign_key_violations"] == 0
    backup_healthy = right["integrity_check"] == "ok" and right["foreign_key_violations"] == 0
    exact_match = all((table_names_match, counts_match, hashes_match, sequences_match,
                       schema_match, source_healthy, backup_healthy))
    portability = build_postgres_portability_report(left["schema_manifest"])
    runtime = audit_source_tree(repository_root)
    left.pop("schema_manifest")
    right.pop("schema_manifest")
    return {
        "format_version": 1,
        "source_engine": "sqlite",
        "target_engine": "postgresql",
        "source": left,
        "backup": right,
        "comparison": {
            "table_names_match": table_names_match,
            "schema_matches": schema_match,
            "row_counts_match": counts_match,
            "row_hashes_match": hashes_match,
            "sequence_counters_match": sequences_match,
            "source_healthy": source_healthy,
            "backup_healthy": backup_healthy,
            "exact_match": exact_match,
        },
        "postgresql_schema_portability": portability,
        "postgresql_runtime_compatibility": runtime,
        "ready_for_shadow_migration": exact_match,
        "postgresql_cutover_allowed": False,
        "cutover_note": (
            "An exact SQLite backup match is only a prerequisite. PostgreSQL schema, "
            "data import, application queries, transaction behavior, concurrency, "
            "restore and rollback require separate verification before cutover."
        ),
    }


def _open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    connection.execute("PRAGMA query_only = ON")
    connection.execute("BEGIN")
    return connection


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Live SQLite database path")
    parser.add_argument("--backup", required=True, help="SQLite backup snapshot path")
    parser.add_argument("--root", default=".", help="Repository root for static SQL audit")
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args()
    source_path = Path(args.source).expanduser().resolve(strict=True)
    backup_path = Path(args.backup).expanduser().resolve(strict=True)
    if source_path == backup_path:
        parser.error("source and backup must be different files")
    output = Path(args.output).expanduser().resolve() if args.output else None
    if output and output in {source_path, backup_path}:
        parser.error("output must not overwrite source or backup")

    source = _open_read_only(source_path)
    backup = _open_read_only(backup_path)
    try:
        report = build_migration_preflight(source, backup, args.root)
    finally:
        source.close()
        backup.close()
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["ready_for_shadow_migration"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
