"""거래 항목 ↔ 첨부파일 — read-only helper.

본 wrapper 는 v0.2.0 부터 attachment storage 의 owner 가 아님 (whooing-tui
가 add/remove 담당, whooing-core 가 storage layer). wrapper 는 audit/list 응답에
`local_attachments` 필드를 augment 할 때만 SELECT.

Storage / CRUD 함수들은 whooing_core.attachments 로 이전됨 (Phase 1).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from whooing_mcp.queue import open_db


def list_attachments_for(
    conn: sqlite3.Connection, entry_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """entry_id → list of attachment rows. 빈 list/None 입력은 빈 dict."""
    if not entry_ids:
        return {}
    placeholders = ",".join("?" * len(entry_ids))
    rows = conn.execute(
        f"""SELECT id, entry_id, section_id, file_path, original_filename,
                   file_size_bytes, file_sha256, mime_type, note, attached_at
            FROM entry_attachments
            WHERE entry_id IN ({placeholders})
            ORDER BY entry_id, attached_at""",
        entry_ids,
    ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        d = dict(r)
        out.setdefault(d["entry_id"], []).append(d)
    return out


def attach_attachments(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """entries 의 각 dict 에 'local_attachments' 필드 추가 (annotation 과 동일 패턴).

    부착되는 형태: list of {id, file_path, original_filename, attached_at, note,
    mime_type, size}. 첨부 없는 entry 는 빈 list.
    """
    if not entries:
        return entries
    ids = [str(e.get("entry_id")) for e in entries if e.get("entry_id")]
    if not ids:
        return [dict(e, local_attachments=[]) for e in entries]
    with open_db() as conn:
        attachments_map = list_attachments_for(conn, ids)
    out = []
    for e in entries:
        eid = str(e.get("entry_id")) if e.get("entry_id") else None
        atts = attachments_map.get(eid, []) if eid else []
        compact = [
            {
                "id": a["id"],
                "file_path": a["file_path"],
                "original_filename": a["original_filename"],
                "mime_type": a["mime_type"],
                "note": a["note"],
                "attached_at": a["attached_at"],
                "size": a["file_size_bytes"],
            }
            for a in atts
        ]
        new_e = dict(e)
        new_e["local_attachments"] = compact
        out.append(new_e)
    return out
