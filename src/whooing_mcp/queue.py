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

SCHEMA_VERSION = 4


def default_queue_path() -> Path:
    """SQLite db 위치.

    우선순위 (v0.2.0):
      1. $WHOOING_QUEUE_PATH                       (legacy explicit override)
      2. $WHOOING_DATA_DIR/whooing-data.sqlite     (NEW — TUI 와 공유 path)
      3. ~/.whooing/whooing-data.sqlite            (NEW default — TUI 와 합의)
      4. <project root>/whooing-data.sqlite        (legacy fallback + warning)

    `WHOOING_DATA_DIR` 는 whooing-tui 와 합의된 단일 root. 같은 machine 의
    두 앱 (wrapper, tui) 이 같은 db / attachments 를 본다.
    """
    import sys
    explicit = os.getenv("WHOOING_QUEUE_PATH")
    if explicit:
        return Path(explicit).expanduser()

    data_dir = os.getenv("WHOOING_DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser() / "whooing-data.sqlite"

    new_default = Path("~/.whooing/whooing-data.sqlite").expanduser()
    if new_default.exists():
        return new_default

    # Legacy fallback — only if user has an existing project-local db
    project_root = Path(__file__).resolve().parents[2]
    legacy = project_root / "whooing-data.sqlite"
    if legacy.exists():
        print(
            f"[whooing-mcp-server] WARNING: 옛 위치의 db 사용 중 ({legacy}).\n"
            f"  마이그레이션 권장 — `mv {legacy} {new_default}` 후 "
            f"`WHOOING_DATA_DIR=~/.whooing` 설정. (또는 무동작 — 본 경고 무시)",
            file=sys.stderr,
        )
        return legacy

    # 새 default — db 가 아예 없으면 (TUI 가 곧 init) 새 path 반환.
    return new_default


def _now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


@contextmanager
def open_db(path: Path | None = None):
    """Yield a read-write connection. Creates schema on first open.

    Used for tables wrapper still owns or shares write access to:
    `pending` (wrapper-only) and `statement_import_log` (shared with TUI —
    wrapper writes via delete_entries to mark `status='deleted'`).
    """
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


@contextmanager
def open_db_ro(path: Path | None = None):
    """Yield a strictly read-only connection (`mode=ro` URI).

    For wrapper helpers that SELECT from annotation/hashtag/entry_attachments
    tables — those are TUI-owned (whooing-tui v0.4.0+ writes via
    whooing_core.db). Any wrapper write attempt raises OperationalError.

    db 가 없으면 (TUI 미실행) FileNotFoundError; caller 가 graceful degrade.
    """
    if path is None:
        path = default_queue_path()
    if not path.exists():
        raise FileNotFoundError(
            f"db 가 없음: {path}. whooing-tui 를 먼저 실행하거나 "
            f"WHOOING_QUEUE_PATH override 확인."
        )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
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

        -- v2: entry annotations (CL 50678)
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

        -- v3: PDF/CSV/외부 명세서 import 추적 (본 CL)
        --     입력한 항목이 어느 PDF, 어느 줄에서 왔는지 + 입력 결과 후잉 entry_id
        --     역추적해 중복 방지 / audit / undo 가능.
        CREATE TABLE IF NOT EXISTS statement_import_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,           -- PDF / CSV 파일 경로
            source_kind TEXT NOT NULL,           -- 'pdf' | 'csv'
            statement_period_start TEXT,         -- YYYYMMDD (명세서 기간)
            statement_period_end TEXT,
            issuer TEXT,                         -- 'shinhan_card' / 'hana_card' 등
            card_label TEXT,                     -- 'VISA3698' / 'MASTER2991' 등 사용자 표기
            entry_date TEXT NOT NULL,            -- YYYYMMDD (사용일)
            merchant TEXT NOT NULL,              -- 가맹점
            original_amount INTEGER NOT NULL,    -- KRW 이용금액 (외화는 카드사 변환된 KRW)
            fee_amount INTEGER NOT NULL DEFAULT 0,  -- KRW 해외이용수수료
            total_amount INTEGER NOT NULL,       -- original + fee (실 후잉 입력 금액)
            currency TEXT NOT NULL DEFAULT 'KRW',  -- 'KRW' | 'USD' | ...
            foreign_amount REAL,                 -- 외화 금액 (현지 통화)
            exchange_rate REAL,                  -- 카드사 환율 (또는 lookup 환율)
            section_id TEXT NOT NULL,
            l_account_id TEXT NOT NULL,
            r_account_id TEXT NOT NULL,
            whooing_entry_id TEXT,               -- POST 성공 시 entry_id (없으면 실패/dry-run)
            status TEXT NOT NULL,                -- 'inserted' | 'failed' | 'dry_run'
            imported_at TEXT NOT NULL,           -- ISO 8601 KST
            error_message TEXT,
            notes TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_import_source ON statement_import_log(source_file);
        CREATE INDEX IF NOT EXISTS idx_import_entry_date ON statement_import_log(entry_date);
        CREATE INDEX IF NOT EXISTS idx_import_whooing_entry ON statement_import_log(whooing_entry_id);

        -- v4: 거래 ↔ 첨부파일 (1:N). 후잉이 entry-attachment 를 미지원해서 본
        -- wrapper 가 별도 layer 로 보관. 파일은 ./attachments/files/YYYY/YYYY-MM-DD/
        -- 하위. 같은 sha256 은 dedup (같은 파일 한 번만 디스크에).
        CREATE TABLE IF NOT EXISTS entry_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL,           -- 후잉 entry_id (외부 키 — 무결성 강제 X)
            section_id TEXT,                   -- 컨텍스트
            file_path TEXT NOT NULL,           -- relative: attachments/files/2026/2026-05-09/foo.pdf
            original_path TEXT,                -- 원본 absolute path (소스 추적)
            original_filename TEXT NOT NULL,
            file_size_bytes INTEGER,
            file_sha256 TEXT,                  -- 중복 감지
            mime_type TEXT,
            note TEXT,                         -- 첨부 사유 / 사용자 메모
            attached_at TEXT NOT NULL          -- ISO 8601 KST
        );
        CREATE INDEX IF NOT EXISTS idx_attach_entry_id ON entry_attachments(entry_id);
        CREATE INDEX IF NOT EXISTS idx_attach_sha256 ON entry_attachments(file_sha256);

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
