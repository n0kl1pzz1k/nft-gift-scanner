"""Persistent index of authoritative Portals gift metadata.

This stores catalogue metadata snapshots only. It does not store NFT assets,
prove wallet ownership, or accept custody/transfer instructions.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from marketplace import _db


def _ensure_table() -> None:
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS catalogue_gifts (
                gift_id TEXT PRIMARY KEY,
                gift_json TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'Portals',
                first_seen_at INTEGER NOT NULL,
                last_seen_at INTEGER NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_catalogue_gifts_last_seen ON catalogue_gifts(last_seen_at DESC)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS catalogue_gift_snapshots (
                gift_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                gift_json TEXT NOT NULL,
                observed_at INTEGER NOT NULL,
                PRIMARY KEY(gift_id, fingerprint)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_catalogue_snapshots_gift_time ON catalogue_gift_snapshots(gift_id, observed_at DESC)"
        )


def upsert_catalogue_items(items: list[dict[str, Any]]) -> int:
    """Persist validated-by-source catalogue metadata returned by Portals."""
    _ensure_table()
    now = int(time.time())
    saved = 0
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_id = item.get("id")
            if raw_id is None or not str(raw_id).strip():
                continue
            gift_id = str(raw_id).strip()
            if len(gift_id) > 160:
                continue
            encoded = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > 32000:
                continue
            fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            conn.execute(
                """INSERT OR IGNORE INTO catalogue_gift_snapshots(gift_id, fingerprint, gift_json, observed_at)
                   VALUES (?, ?, ?, ?)""",
                (gift_id, fingerprint, encoded, now),
            )
            conn.execute(
                """INSERT INTO catalogue_gifts(gift_id, gift_json, source, first_seen_at, last_seen_at)
                   VALUES (?, ?, 'Portals', ?, ?)
                   ON CONFLICT(gift_id) DO UPDATE SET
                     gift_json=excluded.gift_json,
                     source='Portals',
                     last_seen_at=excluded.last_seen_at""",
                (gift_id, encoded, now, now),
            )
            saved += 1
        conn.commit()
    return saved


def get_catalogue_item(gift_id: str) -> dict[str, Any] | None:
    _ensure_table()
    safe_id = str(gift_id or "").strip()
    if not safe_id or len(safe_id) > 160:
        return None
    with _db() as conn:
        row = conn.execute(
            "SELECT gift_json, source, first_seen_at, last_seen_at FROM catalogue_gifts WHERE gift_id = ?",
            (safe_id,),
        ).fetchone()
    if row is None:
        return None
    try:
        item = json.loads(row["gift_json"])
    except (TypeError, ValueError):
        return None
    if not isinstance(item, dict):
        return None
    return {
        **item,
        "catalogue_source": row["source"],
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
    }



def search_catalogue_items(
    query: str = "",
    collection_id: str = "",
    limit: int = 24,
    offset: int = 0,
) -> dict[str, Any]:
    """Search the locally persisted Portals metadata index with bounded pagination."""
    _ensure_table()
    safe_query = str(query or "").strip()[:80]
    safe_collection = str(collection_id or "").strip()[:160]
    safe_limit = min(max(int(limit), 1), 50)
    safe_offset = min(max(int(offset), 0), 100000)
    clauses: list[str] = []
    params: list[Any] = []
    if safe_query:
        pattern = f"%{safe_query}%"
        clauses.append("(json_extract(gift_json, '$.name') LIKE ? OR gift_id LIKE ? OR json_extract(gift_json, '$.number') LIKE ?)")
        params.extend([pattern, pattern, pattern])
    if safe_collection:
        clauses.append("json_extract(gift_json, '$.collection_id') = ?")
        params.append(safe_collection)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with _db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM catalogue_gifts{where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT gift_json, source, first_seen_at, last_seen_at FROM catalogue_gifts{where} ORDER BY last_seen_at DESC, gift_id ASC LIMIT ? OFFSET ?",
            [*params, safe_limit, safe_offset],
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            item = json.loads(row["gift_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(item, dict):
            continue
        items.append({
            **item,
            "catalogue_source": row["source"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
        })
    return {"items": items, "total_count": total, "limit": safe_limit, "offset": safe_offset}



def get_catalogue_history(gift_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return distinct source metadata snapshots, newest first."""
    _ensure_table()
    safe_id = str(gift_id or "").strip()[:160]
    safe_limit = min(max(int(limit), 1), 50)
    if not safe_id or len(safe_id) > 160:
        return []
    with _db() as conn:
        rows = conn.execute(
            """SELECT gift_json, observed_at FROM catalogue_gift_snapshots
               WHERE gift_id = ? ORDER BY observed_at DESC, fingerprint DESC LIMIT ?""",
            (safe_id, safe_limit),
        ).fetchall()
    history: list[dict[str, Any]] = []
    for row in rows:
        try:
            item = json.loads(row["gift_json"])
        except (TypeError, ValueError):
            continue
        if isinstance(item, dict):
            history.append({"item": item, "observed_at": row["observed_at"]})
    return history
