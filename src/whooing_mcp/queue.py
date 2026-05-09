"""SQLite-backed pending queue for SMS/메일 임시 저장 (DESIGN §14 P2).

후잉 공식 자동입력 대기열은 외부 API 로 노출되지 않으므로, 본 wrapper 가
별도의 로컬 큐를 운영한다. 후잉 자체 큐와는 **완전히 별개**.

Storage:
  default: ~/.local/share/whooing-mcp/queue.db (XDG-style)
  override: $WHOOING_QUEUE_PATH
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from whooing_mcp.dates import KST
from datetime import datetime

SCHEMA_VERSION = 2


def default_queue_path() -> Path:
    explicit = os.getenv("WHOOING_QUEUE_PATH")
    if explicit:
        return Path(explicit).expanduser()
    return Path.home() / ".local" / "share" / "whooing-mcp" / "queue.db"


def _now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


@contextmanager
def open_db(path: Path | None = None):
    """Yield a connection. Creates schema on first open."""
    if path is None:
        path = default_queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Schema v1 (CL 50660) + v2 (annotations, 본 CL).

    CREATE IF NOT EXISTS 라 기존 v1 db 도 그대로 마이그레이션.
    """
    conn.executescript(
        """
        -- v1: pending queue (CL 50660)
        CREATE TABLE IF NOT EXISTS pending (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            raw_text TEXT,
            parsed_json TEXT,
            issuer TEXT,
            queued_at TEXT NOT NULL,
            section_id TEXT,
            note TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_pending_queued_at
            ON pending(queued_at);

        -- v2: entry annotations (본 CL)
        CREATE TABLE IF NOT EXISTS entry_annotations (
            entry_id TEXT PRIMARY KEY,
            section_id TEXT,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entry_hashtags (
            entry_id TEXT NOT NULL,
            tag TEXT NOT NULL,
            PRIMARY KEY (entry_id, tag),
            FOREIGN KEY (entry_id) REFERENCES entry_annotations(entry_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_hashtags_tag ON entry_hashtags(tag);

        -- meta
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        -- foreign keys 활성화 (per-connection)
        """
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('version', ?)",
        (str(SCHEMA_VERSION),),
    )


# ---- CRUD helpers -------------------------------------------------------


def insert(
    conn: sqlite3.Connection,
    *,
    source: str,
    raw_text: str | None,
    parsed: dict[str, Any] | None,
    issuer: str | None,
    section_id: str | None,
    note: str | None,
) -> dict[str, Any]:
    queued_at = _now_iso()
    parsed_json = json.dumps(parsed, ensure_ascii=False) if parsed else None
    cur = conn.execute(
        """
        INSERT INTO pending
          (source, raw_text, parsed_json, issuer, queued_at, section_id, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (source, raw_text, parsed_json, issuer, queued_at, section_id, note),
    )
    pending_id = cur.lastrowid
    return {
        "pending_id": pending_id,
        "queued_at": queued_at,
        "source": source,
        "issuer": issuer,
        "section_id": section_id,
    }


def list_items(
    conn: sqlite3.Connection,
    *,
    source: str | None = None,
    since: str | None = None,  # ISO 8601 — only items queued >= since
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM pending WHERE 1=1"
    params: list[Any] = []
    if source:
        sql += " AND source = ?"
        params.append(source)
    if since:
        sql += " AND queued_at >= ?"
        params.append(since)
    sql += " ORDER BY queued_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        if item.get("parsed_json"):
            try:
                item["parsed"] = json.loads(item["parsed_json"])
            except json.JSONDecodeError:
                item["parsed"] = None
        else:
            item["parsed"] = None
        # raw json string 은 파생 필드 노출이 의도. 원본도 보존.
        out.append(item)
    return out


def delete(conn: sqlite3.Connection, pending_id: int) -> dict[str, Any] | None:
    """Returns the deleted row (or None if not found)."""
    row = conn.execute(
        "SELECT * FROM pending WHERE id = ?", (pending_id,)
    ).fetchone()
    if not row:
        return None
    conn.execute("DELETE FROM pending WHERE id = ?", (pending_id,))
    item = dict(row)
    if item.get("parsed_json"):
        try:
            item["parsed"] = json.loads(item["parsed_json"])
        except json.JSONDecodeError:
            item["parsed"] = None
    return item


def count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM pending").fetchone()[0]
