"""Read-only inventory of SQLite-specific runtime assumptions.

This is a source-code audit, not a migration engine. It never opens the
application database or reads user data.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import re
import tokenize
from pathlib import Path
from typing import Any

SOURCE_FILES = (
    "app.py",
    "bot.py",
    "database.py",
    "marketplace.py",
    "marketplace_offers.py",
    "telegram_business_gift_inventory.py",
    "telegram_gift_inventory.py",
    "ton_connect.py",
    "ton_nft_inventory.py",
)
RULES = (
    ("sqlite_connection_factory", re.compile(r"\bsqlite3\s*\.\s*connect\s*\(", re.IGNORECASE), "blocker",
     "Replace direct SQLite connection factories with an explicit backend connection boundary."),
    ("sqlite_transaction_lock", re.compile(r"\bBEGIN\s+IMMEDIATE\b", re.IGNORECASE), "blocker",
     "PostgreSQL has no BEGIN IMMEDIATE; redesign locking and isolation explicitly."),
    ("sqlite_pragma", re.compile(r"\bPRAGMA\s+", re.IGNORECASE), "blocker",
     "PRAGMA is SQLite-specific; replace with engine-specific connection setup or metadata queries."),
    ("sqlite_master_catalog", re.compile(r"\bsqlite_master\b", re.IGNORECASE), "blocker",
     "SQLite catalog queries must be replaced with PostgreSQL information_schema or pg_catalog queries."),
    ("sqlite_script_execution", re.compile(r"\.executescript\s*\(", re.IGNORECASE), "blocker",
     "executescript transaction behavior must be replaced with explicit, portable migrations."),
    ("sqlite_trigger_abort", re.compile(r"\bRAISE\s*\(\s*ABORT\b", re.IGNORECASE), "blocker",
     "SQLite trigger RAISE(ABORT) bodies require PostgreSQL trigger functions and exception semantics."),
    ("sqlite_randomblob_id", re.compile(r"\b(?:lower\s*\(\s*)?hex\s*\(\s*randomblob\s*\(", re.IGNORECASE), "blocker",
     "SQLite randomblob()/hex() ID generation needs a PostgreSQL-compatible UUID or cryptographic ID strategy."),
    ("sqlite_insert_or_ignore", re.compile(r"\bINSERT\s+OR\s+IGNORE\b", re.IGNORECASE), "review",
     "Translate conflict targets and ignored-constraint semantics deliberately."),
    ("sqlite_insert_or_replace", re.compile(r"\bINSERT\s+OR\s+REPLACE\b", re.IGNORECASE), "blocker",
     "SQLite REPLACE may delete then insert; PostgreSQL needs an explicit conflict-update policy."),
    ("sqlite_autoincrement", re.compile(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE), "review",
     "Map generated identifiers and sequence preservation explicitly."),
    ("sqlite_row_factory", re.compile(r"\brow_factory\s*=\s*sqlite3\.Row\b", re.IGNORECASE), "review",
     "Preserve mapping-style row access in the PostgreSQL adapter."),
    ("sqlite_qmark_parameters", re.compile(r"(?<!\?)\?(?!\?)"), "review",
     "Question-mark bind parameters need a safe driver-aware translation layer."),
    ("sqlite_integrity_error", re.compile(r"\bsqlite3\.IntegrityError\b", re.IGNORECASE), "review",
     "Map driver-specific integrity exceptions to domain-level errors."),
    ("sqlite_lastrowid", re.compile(r"\.lastrowid\b", re.IGNORECASE), "review",
     "Use INSERT ... RETURNING for PostgreSQL-generated identifiers."),
    ("sqlite_rowid", re.compile(r"\browid\b", re.IGNORECASE), "blocker",
     "SQLite rowid pseudocolumns do not exist in PostgreSQL; add an explicit stable sequence column."),
)


def _mask_python_comments(source: str) -> str:
    """Blank Python comments while preserving every character offset/newline."""
    lines = source.splitlines(keepends=True)
    offsets: list[int] = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line)

    characters = list(source)
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            start_line, start_column = token.start
            end_line, end_column = token.end
            start = offsets[start_line - 1] + start_column
            end = offsets[end_line - 1] + end_column
            for index in range(start, end):
                if characters[index] not in "\r\n":
                    characters[index] = " "
    except (tokenize.TokenError, IndentationError):
        # Preserve offsets and at least mask standalone comments when an
        # incomplete source file prevents full tokenization.
        return re.sub(r"(?m)^[ \\t]*#.*$", lambda match: " " * len(match.group()), source)
    return "".join(characters)


def _mask_python_docstrings(source: str) -> str:
    """Blank module/class/function docstrings while preserving offsets and SQL literals."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    lines = source.splitlines(keepends=True)
    offsets: list[int] = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line)

    characters = list(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.body or not isinstance(node.body[0], ast.Expr):
            continue
        value = node.body[0].value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        start_line, end_line = value.lineno, value.end_lineno
        start_prefix = lines[start_line - 1].encode("utf-8")[:value.col_offset].decode("utf-8")
        end_prefix = lines[end_line - 1].encode("utf-8")[:value.end_col_offset].decode("utf-8")
        start = offsets[start_line - 1] + len(start_prefix)
        end = offsets[end_line - 1] + len(end_prefix)
        for index in range(start, end):
            if characters[index] not in "\r\n":
                characters[index] = " "
    return "".join(characters)


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
        source = _mask_python_docstrings(_mask_python_comments(path.read_text(encoding="utf-8")))
        file_findings: list[dict[str, Any]] = []
        for feature, pattern, severity, remediation in RULES:
            for match in pattern.finditer(source):
                file_findings.append({
                    "file": relative,
                    "line": source.count("\n", 0, match.start()) + 1,
                    "feature": feature,
                    "severity": severity,
                    "remediation": remediation,
                })
        findings.extend(sorted(file_findings, key=lambda item: (item["line"], item["feature"])))
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
            "Python comments are excluded; SQL comments inside string literals may still produce conservative matches.",
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
