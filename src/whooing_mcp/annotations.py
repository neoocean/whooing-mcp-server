"""거래 항목 별 로컬 annotation (note + hashtags) 저장소.

후잉 자체 memo 가 한 줄짜리 + 해시태그 검색이 부족해서 본 wrapper 가 별도로
운영. SQLite db 는 queue 와 같은 파일 공유 (queue.py 의 open_db 재사용).

Schema:
  entry_annotations (entry_id PK, section_id, note, created_at, updated_at)
  entry_hashtags (entry_id, tag, PRIMARY KEY (entry_id, tag))
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from typing import Any

from whooing_mcp.dates import KST
from whooing_mcp.queue import open_db


def _now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def normalize_hashtag(raw: str) -> str:
    """'#식비' / ' food ' / 'work-trip' → 정규화 형태 ('식비', 'food', 'work-trip').

    raises ValueError on empty / 내부 공백 포함.
    """
    s = raw.strip().lstrip("#").strip()
    if not s:
        raise ValueError(f"empty hashtag: {raw!r}")
    if re.search(r"\s", s):
        raise ValueError(f"hashtag 내부 공백 금지: {raw!r}")
    return s


def parse_hashtag_input(value: str | list[str] | None) -> list[str]:
    """'#식비 #출장' or ['식비', '#출장'] 둘 다 허용. dedupe 보존-순서."""
    if value is None:
        return []
    if isinstance(value, str):
        # 공백/콤마로 분리
        raw_tokens = re.split(r"[\s,]+", value.strip())
    elif isinstance(value, list):
        raw_tokens = value
    else:
        raise ValueError(f"hashtags 는 str 또는 list[str] (받음: {type(value).__name__})")

    seen = set()
    out = []
    for t in raw_tokens:
        if not t or not str(t).strip():
            continue
        norm = normalize_hashtag(str(t))
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


# ---- CRUD ---------------------------------------------------------------


def upsert_annotation(
    conn: sqlite3.Connection,
    *,
    entry_id: str,
    note: str | None = None,
    hashtags: list[str] | None = None,
    section_id: str | None = None,
) -> dict[str, Any]:
    """note / hashtags 둘 중 하나 이상 변경. None 인 필드는 기존 값 보존.

    hashtags 가 빈 list ([]) 면 모든 태그 삭제 (None 과 다름).
    """
    if note is None and hashtags is None:
        raise ValueError("note 또는 hashtags 중 하나는 제공해야 함")

    now = _now_iso()
    existing = conn.execute(
        "SELECT note, created_at, section_id FROM entry_annotations WHERE entry_id = ?",
        (entry_id,),
    ).fetchone()

    if existing:
        new_note = note if note is not None else existing["note"]
        new_section = section_id if section_id is not None else existing["section_id"]
        conn.execute(
            "UPDATE entry_annotations SET note = ?, section_id = ?, updated_at = ? WHERE entry_id = ?",
            (new_note, new_section, now, entry_id),
        )
    else:
        conn.execute(
            """INSERT INTO entry_annotations
                 (entry_id, section_id, note, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (entry_id, section_id, note or "", now, now),
        )

    if hashtags is not None:
        # full replace (sparse update 보다 단순)
        conn.execute("DELETE FROM entry_hashtags WHERE entry_id = ?", (entry_id,))
        for tag in hashtags:
            conn.execute(
                "INSERT OR IGNORE INTO entry_hashtags (entry_id, tag) VALUES (?, ?)",
                (entry_id, tag),
            )

    # 결과 반환
    return get_annotation(conn, entry_id) or {}


def get_annotation(conn: sqlite3.Connection, entry_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM entry_annotations WHERE entry_id = ?", (entry_id,)
    ).fetchone()
    if not row:
        return None
    tags = [
        r["tag"]
        for r in conn.execute(
            "SELECT tag FROM entry_hashtags WHERE entry_id = ? ORDER BY tag",
            (entry_id,),
        ).fetchall()
    ]
    return {
        "entry_id": row["entry_id"],
        "section_id": row["section_id"],
        "note": row["note"] or "",
        "hashtags": tags,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_annotations(
    conn: sqlite3.Connection, entry_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """entry_id → annotation. 없는 ID 는 결과에서 누락."""
    if not entry_ids:
        return {}
    placeholders = ",".join("?" * len(entry_ids))
    rows = conn.execute(
        f"SELECT * FROM entry_annotations WHERE entry_id IN ({placeholders})",
        entry_ids,
    ).fetchall()
    if not rows:
        return {}
    # tags 한 번에 fetch
    tag_rows = conn.execute(
        f"SELECT entry_id, tag FROM entry_hashtags "
        f"WHERE entry_id IN ({placeholders}) ORDER BY tag",
        entry_ids,
    ).fetchall()
    tags_by_id: dict[str, list[str]] = {}
    for tr in tag_rows:
        tags_by_id.setdefault(tr["entry_id"], []).append(tr["tag"])

    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        eid = r["entry_id"]
        out[eid] = {
            "entry_id": eid,
            "section_id": r["section_id"],
            "note": r["note"] or "",
            "hashtags": tags_by_id.get(eid, []),
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
    return out


def delete_annotation(conn: sqlite3.Connection, entry_id: str) -> bool:
    """True if removed, False if not found."""
    cur = conn.execute("DELETE FROM entry_annotations WHERE entry_id = ?", (entry_id,))
    # entry_hashtags 는 ON DELETE CASCADE — 자동 삭제 (PRAGMA foreign_keys=ON 가정)
    # 안전을 위해 명시적 삭제도 한 번
    conn.execute("DELETE FROM entry_hashtags WHERE entry_id = ?", (entry_id,))
    return cur.rowcount > 0


def list_all_hashtags(
    conn: sqlite3.Connection, prefix: str | None = None
) -> list[dict[str, Any]]:
    """모든 unique 해시태그 + 사용 횟수. prefix 로 시작 매칭 필터."""
    if prefix:
        norm = normalize_hashtag(prefix) if prefix.strip() else ""
        rows = conn.execute(
            "SELECT tag, COUNT(*) AS count FROM entry_hashtags "
            "WHERE tag LIKE ? GROUP BY tag ORDER BY count DESC, tag",
            (norm + "%",),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT tag, COUNT(*) AS count FROM entry_hashtags "
            "GROUP BY tag ORDER BY count DESC, tag"
        ).fetchall()
    return [{"tag": r["tag"], "count": r["count"]} for r in rows]


def find_entry_ids_by_hashtag(conn: sqlite3.Connection, tag: str) -> list[str]:
    """특정 해시태그가 붙은 entry_id 목록."""
    norm = normalize_hashtag(tag)
    rows = conn.execute(
        "SELECT entry_id FROM entry_hashtags WHERE tag = ? ORDER BY entry_id",
        (norm,),
    ).fetchall()
    return [r["entry_id"] for r in rows]


# ---- helper for tools ---------------------------------------------------


def attach_annotations(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """후잉 entries dict 리스트의 각 항목에 'local_annotations' 필드 추가.

    로컬 데이터 없으면 None. 원본 dict 를 새 dict 로 복제 (입력 mutate X).
    """
    if not entries:
        return entries

    ids = [e.get("entry_id") for e in entries if e.get("entry_id")]
    if not ids:
        return [dict(e, local_annotations=None) for e in entries]

    with open_db() as conn:
        annos = get_annotations(conn, ids)

    out = []
    for e in entries:
        eid = e.get("entry_id")
        anno = annos.get(eid) if eid else None
        # 부수 정보만 노출 (created_at 등 db 메타는 제외해서 컴팩트)
        if anno:
            local = {"note": anno["note"], "hashtags": anno["hashtags"]}
        else:
            local = None
        new_e = dict(e)
        new_e["local_annotations"] = local
        out.append(new_e)
    return out
