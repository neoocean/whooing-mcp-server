"""거래 별 로컬 annotation (메모 + 해시태그) — read-only helper.

본 wrapper 는 v0.2.0 부터 annotation tables 의 owner 가 아님 (whooing-tui
가 schema 생성 / write 담당). wrapper 는 audit/list 응답에 `local_annotations`
필드를 augment 할 때만 SELECT.

CRUD / parse / normalize 함수들 (v0.1.x 잔재) 은 Phase 2.3 에서 제거됨.
필요 시 whooing_core.db 의 동등 함수 사용.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from whooing_mcp.queue import open_db


def get_annotations(
    conn: sqlite3.Connection, entry_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """{entry_id: {note, hashtags: [...]}} — 한 query 로 batch.

    빈 list / None 입력은 빈 dict 반환. 매칭 없으면 그 ID 는 dict 에서 누락.
    """
    if not entry_ids:
        return {}
    placeholders = ",".join("?" * len(entry_ids))
    notes_rows = conn.execute(
        f"SELECT entry_id, section_id, note, created_at, updated_at "
        f"FROM entry_annotations WHERE entry_id IN ({placeholders})",
        entry_ids,
    ).fetchall()
    tag_rows = conn.execute(
        f"SELECT entry_id, tag FROM entry_hashtags WHERE entry_id IN ({placeholders})",
        entry_ids,
    ).fetchall()
    out: dict[str, dict[str, Any]] = {r["entry_id"]: dict(r) for r in notes_rows}
    for r in tag_rows:
        out.setdefault(r["entry_id"], {"entry_id": r["entry_id"]})\
           .setdefault("hashtags", []).append(r["tag"])
    return out


def attach_annotations(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """entries 의 각 dict 에 'local_annotations' 필드 추가 (audit 응답 augmentation).

    파일 / 메모 없는 entry 는 빈 dict (`{"note": None, "hashtags": []}`).
    SQLite WAL + busy_timeout 으로 TUI write 와 동시 SELECT 안전.
    """
    if not entries:
        return entries
    ids = [str(e.get("entry_id")) for e in entries if e.get("entry_id")]
    if not ids:
        return [
            dict(e, local_annotations={"note": None, "hashtags": []})
            for e in entries
        ]
    with open_db() as conn:
        annotations_map = get_annotations(conn, ids)
    out = []
    for e in entries:
        eid = str(e.get("entry_id")) if e.get("entry_id") else None
        a = annotations_map.get(eid, {}) if eid else {}
        new_e = dict(e)
        new_e["local_annotations"] = {
            "note": a.get("note"),
            "hashtags": a.get("hashtags", []),
        }
        out.append(new_e)
    return out
