"""Small, fail-closed DB-API compatibility layer for staged PostgreSQL adoption.

This adapter does not make the marketplace PostgreSQL-ready by itself. It
preserves the common execute/fetch and SQLite-row access patterns while
rejecting SQLite-only SQL that requires an explicit domain-level rewrite.
The production connection factory remains SQLite until all such rewrites and
concurrency tests are complete.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Sequence


_SQLITE_ONLY = (
    (re.compile(r"^\s*PRAGMA\b", re.IGNORECASE), "PRAGMA is SQLite-only"),
    (re.compile(r"\bsqlite_(?:master|sequence)\b", re.IGNORECASE), "SQLite catalog access is not supported"),
    (re.compile(r"\bBEGIN\s+IMMEDIATE\b", re.IGNORECASE), "BEGIN IMMEDIATE needs an explicit PostgreSQL locking strategy"),
    (re.compile(r"\bINSERT\s+OR\s+(?:IGNORE|REPLACE)\b", re.IGNORECASE), "SQLite INSERT OR semantics need an explicit PostgreSQL conflict policy"),
    (re.compile(r"\bRAISE\s*\(", re.IGNORECASE), "SQLite trigger RAISE() semantics need a PostgreSQL trigger function"),
    (re.compile(r"\browid\b", re.IGNORECASE), "SQLite rowid is not available in PostgreSQL"),
)


def translate_qmark_sql(statement: str, parameters: Sequence[Any] | None = None) -> str:
    """Translate positional qmark binds outside SQL literals/comments to %s.

    The scanner understands SQL single/double quotes, escaped quote pairs,
    line/block comments, and dollar-quoted bodies. PostgreSQL JSON existence
    operators (? / ?| / ?&) are preserved when they are visibly operators.
    A strict placeholder-count check prevents silently binding the wrong value.
    """
    if not isinstance(statement, str):
        raise TypeError("SQL statement must be a string")
    if parameters is None:
        parameter_count = 0
    elif isinstance(parameters, (str, bytes, bytearray, dict)):
        raise TypeError("SQL parameters must be a positional sequence")
    else:
        parameter_count = len(parameters)

    for pattern, message in _SQLITE_ONLY:
        if pattern.search(statement):
            raise NotImplementedError(message)

    output: list[str] = []
    positions: list[int] = []
    i = 0
    n = len(statement)
    state = "normal"
    dollar_tag = ""

    while i < n:
        char = statement[i]
        nxt = statement[i + 1] if i + 1 < n else ""

        if state == "single":
            output.append(char)
            if char == "'" and nxt == "'":
                output.append(nxt)
                i += 2
                continue
            if char == "'":
                state = "normal"
            i += 1
            continue
        if state == "double":
            output.append(char)
            if char == '"' and nxt == '"':
                output.append(nxt)
                i += 2
                continue
            if char == '"':
                state = "normal"
            i += 1
            continue
        if state == "line_comment":
            output.append(char)
            if char in "\r\n":
                state = "normal"
            i += 1
            continue
        if state == "block_comment":
            if char == "*" and nxt == "/":
                output.extend((char, nxt))
                i += 2
                state = "normal"
            else:
                output.append(char)
                i += 1
            continue
        if state == "dollar":
            if statement.startswith(dollar_tag, i):
                output.append(dollar_tag)
                i += len(dollar_tag)
                state = "normal"
            else:
                output.append(char)
                i += 1
            continue

        if char == "'":
            state = "single"
            output.append(char)
            i += 1
            continue
        if char == '"':
            state = "double"
            output.append(char)
            i += 1
            continue
        if char == "-" and nxt == "-":
            state = "line_comment"
            output.extend((char, nxt))
            i += 2
            continue
        if char == "/" and nxt == "*":
            state = "block_comment"
            output.extend((char, nxt))
            i += 2
            continue
        if char == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", statement[i:])
            if match:
                dollar_tag = match.group(0)
                state = "dollar"
                output.append(dollar_tag)
                i += len(dollar_tag)
                continue
        if char == "?":
            # Preserve PostgreSQL JSONB operators: ?, ?|, and ?&.
            following = statement[i + 1] if i + 1 < n else ""
            before = statement[i - 1] if i else ""
            if following in ("|", "&") or following == "?" or before == "?":
                output.append(char)
                i += 1
                continue
            lookahead = i + 1
            while lookahead < n and statement[lookahead].isspace():
                lookahead += 1
            if lookahead < n and statement[lookahead] == "'":
                output.append(char)
                i += 1
                continue
            positions.append(i)
            output.append("%s")
            i += 1
            continue

        output.append(char)
        i += 1

    if len(positions) != parameter_count:
        raise ValueError(
            f"SQL placeholder count mismatch: found {len(positions)} positional "
            f"placeholder(s), received {parameter_count} parameter(s)"
        )
    return "".join(output)


class HybridRow:
    """Row supporting both SQLite-style numeric and mapping-style indexing."""

    __slots__ = ("_values", "_names", "_index")

    def __init__(self, names: Sequence[str], values: Sequence[Any]):
        self._values = tuple(values)
        self._names = tuple(names)
        self._index = {name: index for index, name in enumerate(self._names)}

    def __getitem__(self, key: int | slice | str) -> Any:
        if isinstance(key, str):
            try:
                key = self._index[key]
            except KeyError:
                raise IndexError(key) from None
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> tuple[str, ...]:
        return self._names

    def values(self) -> tuple[Any, ...]:
        return self._values

    def items(self) -> tuple[tuple[str, Any], ...]:
        return tuple(zip(self._names, self._values))

    def get(self, key: str, default: Any = None) -> Any:
        index = self._index.get(key)
        return default if index is None else self._values[index]

    def __repr__(self) -> str:
        return f"HybridRow({dict(self.items())!r})"


class PostgresCursorAdapter:
    """Wrap a psycopg cursor and expose SQLite-compatible row access."""

    def __init__(self, cursor: Any):
        self._cursor = cursor

    def _row(self, values: Any) -> HybridRow | None:
        if values is None:
            return None
        names = [column.name if hasattr(column, "name") else column[0] for column in self._cursor.description]
        return HybridRow(names, values)

    def fetchone(self) -> HybridRow | None:
        return self._row(self._cursor.fetchone())

    def fetchall(self) -> list[HybridRow]:
        return [self._row(row) for row in self._cursor.fetchall()]

    def fetchmany(self, size: int | None = None) -> list[HybridRow]:
        rows = self._cursor.fetchmany() if size is None else self._cursor.fetchmany(size)
        return [self._row(row) for row in rows]

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def description(self) -> Any:
        return self._cursor.description

    def __iter__(self):
        for row in self._cursor:
            yield self._row(row)


class PostgresConnectionAdapter:
    """Minimal connection facade; not yet enabled for marketplace runtime."""

    def __init__(self, connection: Any):
        self._connection = connection

    def execute(self, statement: str, parameters: Sequence[Any] | None = None) -> PostgresCursorAdapter:
        translated = translate_qmark_sql(statement, parameters)
        cursor = self._connection.execute(translated, () if parameters is None else parameters)
        return PostgresCursorAdapter(cursor)

    def executemany(self, statement: str, parameter_sets: Iterable[Sequence[Any]]) -> PostgresCursorAdapter:
        batches = list(parameter_sets)
        expected = len(batches[0]) if batches else 0
        translated = translate_qmark_sql(statement, batches[0] if batches else ())
        if any(len(item) != expected for item in batches):
            raise ValueError("executemany parameter rows must have a consistent width")
        cursor = self._connection.cursor()
        cursor.executemany(translated, batches)
        return PostgresCursorAdapter(cursor)

    def executescript(self, _script: str) -> None:
        raise NotImplementedError("executescript is SQLite-specific; use reviewed PostgreSQL migrations")

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    @property
    def lastrowid(self) -> None:
        raise NotImplementedError("PostgreSQL inserts must use RETURNING instead of lastrowid")
