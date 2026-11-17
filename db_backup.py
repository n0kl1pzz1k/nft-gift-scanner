"""Consistent, verifiable SQLite snapshots for safe database migration.

The manifest contains schema and row-count metadata only. It never exports
application records into logs or a separate plaintext report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_metadata(connection: sqlite3.Connection) -> tuple[list[dict[str, str]], str]:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, COALESCE(sql, '')
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    schema = [
        {"type": row[0], "name": row[1], "table": row[2], "sql": row[3]}
        for row in rows
    ]
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return schema, hashlib.sha256(encoded).hexdigest()


def _row_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = connection.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    counts: dict[str, int] = {}
    for (table_name,) in tables:
        quoted_name = '"' + table_name.replace('"', '""') + '"'
        counts[table_name] = int(connection.execute(
            f"SELECT COUNT(*) FROM {quoted_name}"
        ).fetchone()[0])
    return counts


def _sequence_counters(connection: sqlite3.Connection) -> dict[str, int]:
    """Capture AUTOINCREMENT high-water marks without exporting application rows.

    A sequence can exceed MAX(id) after deletions. Preserving it prevents a
    post-migration insert from reusing an identifier that existed historically.
    """
    try:
        rows = connection.execute(
            "SELECT name, seq FROM sqlite_sequence ORDER BY name"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(name): int(sequence) for name, sequence in rows}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def backup_database(source_path: str | Path, destination_path: str | Path) -> dict[str, Any]:
    """Create a transactionally consistent snapshot and a verifiable sidecar manifest.

    Raises ValueError for invalid paths and RuntimeError if the resulting snapshot
    fails SQLite integrity or foreign-key validation.
    """
    source = Path(source_path).expanduser().resolve()
    destination = Path(destination_path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Source database does not exist: {source}")
    if source == destination:
        raise ValueError("Backup destination must differ from the source database")

    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".sqlite3.tmp", dir=str(destination.parent)
    )
    os.close(fd)
    temporary_backup = Path(temporary_name)
    source_connection: sqlite3.Connection | None = None
    backup_connection: sqlite3.Connection | None = None

    try:
        source_uri = source.as_uri() + "?mode=ro"
        source_connection = sqlite3.connect(source_uri, uri=True, timeout=30)
        backup_connection = sqlite3.connect(str(temporary_backup), timeout=30)
        source_connection.backup(backup_connection, pages=256, sleep=0.05)
        backup_connection.commit()

        integrity = backup_connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_violations = backup_connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        if foreign_key_violations:
            raise RuntimeError(
                f"SQLite foreign-key check failed: {len(foreign_key_violations)} violation(s)"
            )

        schema, schema_sha256 = _schema_metadata(backup_connection)
        row_counts = _row_counts(backup_connection)
        backup_connection.close()
        backup_connection = None
        source_connection.close()
        source_connection = None

        backup_sha256 = _sha256_file(temporary_backup)
        manifest: dict[str, Any] = {
            "format_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_file": source.name,
            "backup_file": destination.name,
            "backup_sha256": backup_sha256,
            "schema_sha256": schema_sha256,
            "integrity_check": integrity,
            "foreign_key_violations": 0,
            "table_count": len(row_counts),
            "total_rows": sum(row_counts.values()),
            "tables": row_counts,
            "sequence_counters": _sequence_counters(backup_connection),
            "schema_objects": schema,
        }

        manifest_path = Path(str(destination) + ".manifest.json")
        os.replace(temporary_backup, destination)
        _atomic_write_json(manifest_path, manifest)
        return manifest
    finally:
        if backup_connection is not None:
            backup_connection.close()
        if source_connection is not None:
            source_connection.close()
        temporary_backup.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a verified HASSLE NFT SQLite backup")
    parser.add_argument("--source", help="SQLite source path (defaults to the configured marketplace DB)")
    parser.add_argument("--output", required=True, help="Destination .sqlite3 snapshot path")
    args = parser.parse_args()

    if args.source:
        source_path = args.source
    else:
        from marketplace import DB_PATH
        source_path = DB_PATH

    manifest = backup_database(source_path, args.output)
    print(json.dumps({
        "backup_file": manifest["backup_file"],
        "backup_sha256": manifest["backup_sha256"],
        "manifest_file": manifest["backup_file"] + ".manifest.json",
        "table_count": manifest["table_count"],
        "total_rows": manifest["total_rows"],
        "integrity_check": manifest["integrity_check"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
