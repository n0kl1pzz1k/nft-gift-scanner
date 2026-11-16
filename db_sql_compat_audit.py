"""Read-only inventory of SQLite-specific runtime assumptions.

This is a source-code audit, not a migration engine. It never opens the
application database or reads user data.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SOURCE_FILES = ("marketplace.py", "demo_offers.py", "ton_connect.py")
RULES = (
    ("sqlite_connection_factory", re.compile(r"\bsqlite3\.connect\s*\("), "blocker",
     "Replace direct SQLite connection factories with an explicit backend connection boundary."),
    ("sqlite_transaction_lock", re.compile(r"BEGIN\s+IMMEDIATE", re.IGNORECASE), "blocker",
     "PostgreSQL has no BEGIN IMMEDIATE; redesign locking and isolation explicitly."),
    ("sqlite_pragma", re.compile(r"\bPRAGMA\s+", re.IGNORECASE), "blocker",
     "PRAGMA is SQLite-specific; replace with engine-specific connection setup or metadata queries."),
    ("sqlite_script_execution", re.compile(r"\.executescript\s*\(", re.IGNORECASE), "blocker",
     "executescript transaction behavior must be replaced with explicit, portable migrations."),
    ("sqlite_trigger_abort", re.compile(r"\bRAISE\s*\(\s*ABORT\b", re.IGNORECASE), "blocker",
     "SQLite trigger RAISE(ABORT) bodies require PostgreSQL trigger functions and exception semantics."),
    ("sqlite_randomblob_id", re.compile(r"\b(?:lower\s*\(\s*)?hex\s*\(\s*randomblob\s*\(", re.IGNORECASE), "blocker",
     "SQLite randomblob()/hex() ID generation needs a PostgreSQL-compatible UUID or cryptographic ID strategy."),
    ("sqlite_insert_or_ignore", re.compile(r"INSERT\s+OR\s+IGNORE", re.IGNORECASE), "review",
     "Translate conflict targets and ignored-constraint semantics deliberately."),
    ("sqlite_insert_or_replace", re.compile(r"INSERT\s+OR\s+REPLACE", re.IGNORECASE), "blocker",
     "SQLite REPLACE may delete then insert; PostgreSQL needs an explicit conflict-update policy."),
    ("sqlite_autoincrement", re.compile(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", re.IGNORECASE), "review",
     "Map generated identifiers and sequence preservation explicitly."),
    ("sqlite_row_factory", re.compile(r"row_factory\s*=\s*sqlite3\.Row", re.IGNORECASE), "review",
     "Preserve mapping-style row access in the PostgreSQL adapter."),
    ("sqlite_qmark_parameters", re.compile(r"(?<!\?)\?(?!\?)"), "review",
     "Question-mark bind parameters need a safe driver-aware translation layer."),
    ("sqlite_integrity_error", re.compile(r"sqlite3\.IntegrityError"), "review",
     "Map driver-specific integrity exceptions to domain-level errors."),
    ("sqlite_lastrowid", re.compile(r"\.lastrowid\b"), "review",
     "Use INSERT ... RETURNING for PostgreSQL-generated identifiers."),
    ("sqlite_rowid", re.compile(r"\browid\b", re.IGNORECASE), "blocker",
     "SQLite rowid pseudocolumns do not exist in PostgreSQL; add an explicit stable sequence column."),
)

def audit_source_tree(root: str | Path) -> dict[str, Any]:
    """Return file/line references and counts, never source snippets."""
    root_path = Path(root)
    findings: list[dict[str, Any]] = []
    scanned: list[str] = []
    for relative in SOURCE_FILES:
        path = root_path / relative
        if not path.is_file():
            continue
        scanned.append(relative)
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for feature, pattern, severity, remediation in RULES:
                if pattern.search(line):
                    findings.append({
                        "file": relative,
                        "line": line_number,
                        "feature": feature,
                        "severity": severity,
                        "remediation": remediation,
                    })
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["feature"]] = counts.get(finding["feature"], 0) + 1
    blockers = sum(item["severity"] == "blocker" for item in findings)
    return {
        "source_files": scanned,
        "finding_count": len(findings),
        "blocker_count": blockers,
        "review_count": len(findings) - blockers,
        "ready_for_postgresql_runtime": False,
        "findings": findings,
        "feature_counts": dict(sorted(counts.items())),
        "limitations": [
            "Static source scan only; dynamic SQL and imported helpers may be missed.",
            "Does not inspect or export database rows.",
            "Does not prove transaction, concurrency, or data-migration compatibility.",
        ],
    }

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Repository root")
    parser.add_argument("--output", help="Optional JSON output path")
    args = parser.parse_args()
    report = audit_source_tree(args.root)
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
