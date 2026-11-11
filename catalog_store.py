"""Persistent index of authoritative Portals gift metadata.

This stores catalogue metadata snapshots only. It does not store NFT assets,
prove wallet ownership, or accept custody/transfer instructions.
"""
from __future__ import annotations

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
            gift_id = str(raw_id).strip()[:160]
            encoded = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > 32000:
                continue
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
    safe_id = str(gift_id or "").strip()[:160]
    if not safe_id:
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
