"""Read-only inventory of cursor.rowcount dependencies.

This source audit identifies places where business logic depends on driver-specific
rowcount behavior. It never opens the application database or reads user rows.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

SOURCE_FILES = ("marketplace.py", "marketplace_offers.py", "ton_connect.py")


def _rowcount_context(node: ast.Attribute) -> str:
    parent = getattr(node, "parent", None)
    if isinstance(parent, ast.Compare):
        return "comparison"
    if isinstance(parent, ast.Return):
        return "return_value"
    if isinstance(parent, ast.Assign):
        return "assignment"
    if isinstance(parent, ast.AugAssign):
        return "augmented_assignment"
    if isinstance(parent, ast.Call):
        return "call_argument"
    if isinstance(parent, ast.BoolOp):
        return "boolean_expression"
    if isinstance(parent, ast.If):
        return "branch_condition"
    return "other"


class _RowcountVisitor(ast.NodeVisitor):
    def __init__(self, relative: str):
        self.relative = relative
        self.function_stack: list[str] = []
        self.sql_by_cursor: dict[str, str] = {}
        self.findings: list[dict[str, Any]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        previous = self.sql_by_cursor
        self.sql_by_cursor = {}
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()
        self.sql_by_cursor = previous

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        previous = self.sql_by_cursor
        self.sql_by_cursor = {}
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()
        self.sql_by_cursor = previous

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
            if node.value.func.attr in {"execute", "executemany"} and node.value.args:
                sql_node = node.value.args[0]
                if isinstance(sql_node, ast.Constant) and isinstance(sql_node.value, str):
                    operation = sql_node.value.lstrip().split(None, 1)[0].upper() if sql_node.value.strip() else "UNKNOWN"
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            self.sql_by_cursor[target.id] = operation
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "rowcount":
            self.findings.append({
                "file": self.relative,
                "line": node.lineno,
                "function": self.function_stack[-1] if self.function_stack else "<module>",
                "context": _rowcount_context(node),
                "sql_operation": (
                    self.sql_by_cursor.get(node.value.id, "unresolved")
                    if isinstance(node.value, ast.Name) else "unresolved"
                ),
                "comparison_operator": (
                    type(node.parent.ops[0]).__name__
                    if isinstance(node.parent, ast.Compare) and len(node.parent.ops) == 1
                    else None
                ),
                "severity": "review",
                "remediation": (
                    "Confirm the selected database driver's rowcount semantics for this SQL "
                    "statement; prefer explicit RETURNING/affected-row contracts where correctness depends on it."
                ),
            })
        self.generic_visit(node)


def audit_rowcount_usage(root: str | Path) -> dict[str, Any]:
    root_path = Path(root)
    scanned: list[str] = []
    findings: list[dict[str, Any]] = []
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
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                setattr(child, "parent", node)
        visitor = _RowcountVisitor(relative)
        visitor.visit(tree)
        findings.extend(visitor.findings)

    findings.sort(key=lambda item: (item["file"], item["line"], item["function"]))
    context_counts: dict[str, int] = {}
    for finding in findings:
        context_counts[finding["context"]] = context_counts.get(finding["context"], 0) + 1
    return {
        "format_version": 1,
        "source_files": scanned,
        "rowcount_usage_count": len(findings),
        "context_counts": dict(sorted(context_counts.items())),
        "findings": findings,
        "parse_errors": parse_errors,
        "ready_for_postgresql_runtime": False,
        "limitations": [
            "Static AST inventory only; dynamic dispatch and imported helpers may be missed.",
            "Does not determine actual driver behavior or prove a particular rowcount check is correct.",
            "Does not open the application database or inspect/export user rows.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Repository root")
    parser.add_argument("--output", help="Optional JSON output path")
    args = parser.parse_args()
    report = audit_rowcount_usage(args.root)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 1 if report["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
