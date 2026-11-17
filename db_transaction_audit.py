"""Read-only inventory of database write and transaction boundaries.

This heuristic source audit never opens a database or reads application rows.
It helps identify transaction scopes that must be reviewed before a PostgreSQL
runtime adapter can be considered safe.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any

SOURCE_FILES = ("app.py", "marketplace.py", "marketplace_offers.py", "ton_connect.py")
MUTATION_KINDS = {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"}


def _literal_sql(node: ast.AST) -> tuple[str | None, bool]:
    """Return visible SQL text and whether the expression contains dynamic parts."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, False
    if isinstance(node, ast.JoinedStr):
        pieces: list[str] = []
        dynamic = False
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                pieces.append(value.value)
            else:
                dynamic = True
        return " ".join(pieces), dynamic
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, left_dynamic = _literal_sql(node.left)
        right, right_dynamic = _literal_sql(node.right)
        if left is None and right is None:
            return None, True
        return (left or "") + (right or ""), left_dynamic or right_dynamic
    return None, True


def _strip_sql_comments(sql: str) -> str:
    """Remove SQL comments without touching quoted strings."""
    out: list[str] = []; i = 0; quote: str | None = None
    while i < len(sql):
        ch = sql[i]
        if quote:
            out.append(ch)
            if ch == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote: out.append(sql[i + 1]); i += 1
                else: quote = None
            elif ch == chr(92) and quote in {chr(39), chr(34)} and i + 1 < len(sql): out.append(sql[i + 1]); i += 1
            i += 1; continue
        if ch in {chr(39), chr(34)}: quote = ch; out.append(ch); i += 1; continue
        if ch == '[': quote = ']'; out.append(ch); i += 1; continue
        if sql.startswith('--', i):
            end = sql.find(chr(10), i)
            if end < 0: break
            out.append(chr(10)); i = end + 1; continue
        if sql.startswith('/*', i):
            end = sql.find('*/', i + 2)
            if end < 0: break
            out.append(' '); i = end + 2; continue
        out.append(ch); i += 1
    return ''.join(out)

def _statements(sql: str) -> list[str]:
    clean = _strip_sql_comments(sql); parts: list[str] = []; start = 0; i = 0; quote: str | None = None
    while i < len(clean):
        ch = clean[i]
        if quote:
            if ch == quote:
                if i + 1 < len(clean) and clean[i + 1] == quote: i += 1
                else: quote = None
        elif ch in {chr(39), chr(34)}: quote = ch
        elif ch == '[': quote = ']'
        elif ch == ';':
            if clean[start:i].strip(): parts.append(clean[start:i].strip())
            start = i + 1
        i += 1
    if clean[start:].strip(): parts.append(clean[start:].strip())
    return parts

def _kind(statement: str) -> str | None:
    tokens = re.finditer(r'[A-Za-z_][A-Za-z_0-9]*|[()]', _strip_sql_comments(statement))
    depth = 0; first: str | None = None
    for match in tokens:
        token = match.group(0).upper()
        if token == '(': depth += 1; continue
        if token == ')': depth = max(0, depth - 1); continue
        if first is None:
            first = token
            if first != 'WITH': return first
            continue
        if depth == 0 and token in MUTATION_KINDS | {'SELECT', 'VALUES', 'BEGIN', 'START', 'COMMIT', 'ROLLBACK', 'END'}: return token
    return first

class _FunctionScanner(ast.NodeVisitor):
    def __init__(self, relative_path: str):
        self.relative_path = relative_path
        self.current: dict[str, Any] | None = None
        self.functions: list[dict[str, Any]] = []

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        previous = self.current
        record: dict[str, Any] = {
            "file": self.relative_path,
            "function": node.name,
            "line": node.lineno,
            "mutation_count": 0,
            "mutation_kinds": [],
            "transaction_start_count": 0,
            "commit_count": 0,
            "rollback_count": 0,
            "executescript_count": 0,
            "dynamic_sql_count": 0,
            "connection_context_count": 0,
        }
        self.current = record
        for child in node.body:
            self.visit(child)
        if record["mutation_count"] or record["transaction_start_count"] or record["executescript_count"]:
            record["multi_statement_mutation_without_explicit_begin"] = (
                record["mutation_count"] > 1 and record["transaction_start_count"] == 0 and record["connection_context_count"] == 0
            )
            record["transaction_without_explicit_end"] = (
                record["transaction_start_count"] > 0
                and record["commit_count"] + record["rollback_count"] + record["connection_context_count"] == 0
            )
            record["single_statement_autocommit_write"] = (
                record["mutation_count"] == 1 and record["transaction_start_count"] == 0 and record["connection_context_count"] == 0
            )
            self.functions.append(record)
        self.current = previous

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_With(self, node: ast.With) -> None:
        if self.current is not None:
            for item in node.items:
                context = item.context_expr
                if isinstance(context, ast.Name) and context.id.lower() in {'conn', 'connection', 'db', 'database'}:
                    self.current['connection_context_count'] += 1
                elif isinstance(context, ast.Call):
                    function = context.func
                    function_name = function.id if isinstance(function, ast.Name) else function.attr if isinstance(function, ast.Attribute) else ''
                    if function_name.lower() == 'connect':
                        self.current['connection_context_count'] += 1
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.visit_With(node)
    def visit_Call(self, node: ast.Call) -> None:
        if self.current is None or not isinstance(node.func, ast.Attribute):
            self.generic_visit(node)
            return

        method = node.func.attr.lower()
        if method in {"commit", "rollback"} and not node.args:
            key = "commit_count" if method == "commit" else "rollback_count"
            self.current[key] += 1
        elif method in {"execute", "executemany", "executescript"}:
            if method == "executescript":
                self.current["executescript_count"] += 1
            if not node.args:
                self.current["dynamic_sql_count"] += 1
            else:
                sql, dynamic = _literal_sql(node.args[0])
                if dynamic:
                    self.current["dynamic_sql_count"] += 1
                if sql is None:
                    self.generic_visit(node)
                    return
                for statement in _statements(sql):
                    kind = _kind(statement)
                    if kind in {"BEGIN", "START"}:
                        self.current["transaction_start_count"] += 1
                    elif kind == "COMMIT":
                        self.current["commit_count"] += 1
                    elif kind in {"ROLLBACK", "END"}:
                        self.current["rollback_count"] += 1
                    elif kind in MUTATION_KINDS:
                        self.current["mutation_count"] += 1
                        if kind not in self.current["mutation_kinds"]:
                            self.current["mutation_kinds"].append(kind)
        self.generic_visit(node)


def audit_transaction_boundaries(root: str | Path) -> dict[str, Any]:
    """Summarize write and transaction patterns without returning source snippets."""
    root_path = Path(root)
    scanned: list[str] = []
    functions: list[dict[str, Any]] = []
    parse_errors: list[dict[str, str]] = []

    for relative in SOURCE_FILES:
        path = root_path / relative
        if not path.is_file():
            continue
        scanned.append(relative)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, SyntaxError) as exc:
            parse_errors.append({"file": relative, "error": type(exc).__name__})
            continue
        scanner = _FunctionScanner(relative)
        scanner.visit(tree)
        functions.extend(scanner.functions)

    functions.sort(key=lambda item: (item["file"], item["line"], item["function"]))
    multi_without_begin = [
        item for item in functions if item["multi_statement_mutation_without_explicit_begin"]
    ]
    open_transactions = [
        item for item in functions if item["transaction_without_explicit_end"]
    ]
    return {
        "format_version": 1,
        "source_files": scanned,
        "function_count": len(functions),
        "mutation_function_count": sum(item["mutation_count"] > 0 for item in functions),
        "multi_statement_mutation_without_explicit_begin_count": len(multi_without_begin),
        "transaction_without_explicit_end_count": len(open_transactions),
        "functions": functions,
        "review_items": {
            "multi_statement_mutation_without_explicit_begin": multi_without_begin,
            "transaction_without_explicit_end": open_transactions,
        },
        "parse_errors": parse_errors,
        "ready_for_postgresql_runtime": False,
        "limitations": [
            "Static AST heuristic; dynamic SQL and SQL assembled in imported helpers may be missed.",
            "A function-level count does not prove control-flow atomicity or correct rollback behavior.",
            "Single-statement autocommit writes are reported for review, not classified as unsafe.",
            "SQLite triggers and transaction semantics require separate engine-specific analysis.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Repository root")
    parser.add_argument("--output", help="Optional JSON output path")
    args = parser.parse_args()
    report = audit_transaction_boundaries(args.root)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 1 if report["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
