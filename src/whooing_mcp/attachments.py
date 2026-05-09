"""거래 항목 ↔ 첨부파일 (1:N) — 후잉이 entry-attachment 미지원해서 로컬 보완.

저장 구조:
  <project>/attachments/files/YYYY/YYYY-MM-DD/<filename>

같은 SHA256 (= 같은 파일 내용) 이 이미 있으면 디스크에 재복사 안 함 (db row 만 추가).
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from whooing_mcp.dates import KST
from whooing_mcp.queue import open_db

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _today_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def default_attachments_root() -> Path:
    """attachments/files/ 의 절대 경로. 프로젝트 루트 하위.

    `WHOOING_ATTACHMENTS_ROOT` env 로 override 가능 (테스트용).
    """
    import os
    explicit = os.getenv("WHOOING_ATTACHMENTS_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    project_root = Path(__file__).resolve().parents[2]
    return project_root / "attachments" / "files"


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_to_attachments(
    src_path: Path,
    *,
    attach_date: str | None = None,
) -> tuple[Path, str, int]:
    """src 를 attachments/files/YYYY/YYYY-MM-DD/<basename> 으로 복사.

    Args:
      src_path: 원본 파일 경로
      attach_date: YYYY-MM-DD (default: today). 디렉터리 분류용.

    Returns:
      (copied_path: Path, sha256_hex: str, size_bytes: int)
      copied_path 는 절대 경로.

    Same sha256 이 같은 (date) 폴더에 이미 있으면 재복사 안 함 — 기존 파일 path 반환.
    같은 sha256 이 다른 폴더에 있으면 새 폴더에 hard link (저장 효율).
    """
    src_path = src_path.resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"source file not found: {src_path}")
    if not src_path.is_file():
        raise ValueError(f"not a regular file: {src_path}")

    sha256 = _sha256_of_file(src_path)
    size = src_path.stat().st_size

    date_str = attach_date or _today_str()
    year = date_str[:4]
    target_dir = default_attachments_root() / year / date_str
    target_dir.mkdir(parents=True, exist_ok=True)

    target = target_dir / src_path.name
    # 충돌: 같은 이름이 이미 있고 같은 sha256 면 그대로 reuse
    if target.exists():
        if _sha256_of_file(target) == sha256:
            log.info("attachment already exists at %s (same sha256), reusing", target)
            return target, sha256, size
        # 다른 내용이면 suffix 추가
        i = 1
        while True:
            candidate = target_dir / f"{src_path.stem}-{i}{src_path.suffix}"
            if not candidate.exists():
                target = candidate
                break
            if _sha256_of_file(candidate) == sha256:
                return candidate, sha256, size
            i += 1

    shutil.copy2(src_path, target)
    log.info("copied %s → %s (%d bytes, sha256 %s)", src_path, target, size, sha256[:12])
    return target, sha256, size


def upsert_attachment(
    conn: sqlite3.Connection,
    *,
    entry_id: str,
    section_id: str | None,
    file_path: str,            # relative path (attachments/files/...)
    original_path: str | None,
    original_filename: str,
    file_size_bytes: int | None,
    file_sha256: str | None,
    mime_type: str | None,
    note: str | None,
) -> dict[str, Any]:
    """attach row 를 db 에 삽입 (또는 같은 entry+sha256 이면 기존 row 반환)."""
    if file_sha256:
        existing = conn.execute(
            """SELECT * FROM entry_attachments
               WHERE entry_id = ? AND file_sha256 = ?
               LIMIT 1""",
            (entry_id, file_sha256),
        ).fetchone()
        if existing:
            return dict(existing)

    cur = conn.execute(
        """INSERT INTO entry_attachments
           (entry_id, section_id, file_path, original_path, original_filename,
            file_size_bytes, file_sha256, mime_type, note, attached_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (entry_id, section_id, file_path, original_path, original_filename,
         file_size_bytes, file_sha256, mime_type, note, _now_iso()),
    )
    aid = cur.lastrowid
    row = conn.execute("SELECT * FROM entry_attachments WHERE id = ?", (aid,)).fetchone()
    return dict(row)


def list_attachments_for(
    conn: sqlite3.Connection,
    entry_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """entry_id → list of attachment rows."""
    if not entry_ids:
        return {}
    placeholders = ",".join("?" * len(entry_ids))
    rows = conn.execute(
        f"""SELECT * FROM entry_attachments
            WHERE entry_id IN ({placeholders})
            ORDER BY entry_id, attached_at""",
        entry_ids,
    ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        d = dict(r)
        out.setdefault(d["entry_id"], []).append(d)
    return out


def delete_attachment(
    conn: sqlite3.Connection,
    attachment_id: int,
    *,
    delete_file: bool = True,
) -> dict[str, Any] | None:
    """row 제거 + (옵션) 디스크 파일도 제거.

    같은 sha256 의 다른 row 가 남아있으면 파일은 보존 (다른 entry 가 참조).
    """
    row = conn.execute(
        "SELECT * FROM entry_attachments WHERE id = ?", (attachment_id,)
    ).fetchone()
    if not row:
        return None
    info = dict(row)
    conn.execute("DELETE FROM entry_attachments WHERE id = ?", (attachment_id,))

    if delete_file:
        sha = info.get("file_sha256")
        # 같은 sha256 의 다른 참조가 있는지
        other = conn.execute(
            "SELECT COUNT(*) FROM entry_attachments WHERE file_sha256 = ?",
            (sha,),
        ).fetchone()[0] if sha else 0
        if other == 0:
            project_root = Path(__file__).resolve().parents[2]
            full_path = project_root / info["file_path"]
            try:
                if full_path.exists():
                    full_path.unlink()
                    info["file_deleted"] = True
            except OSError as e:
                info["file_delete_error"] = str(e)
        else:
            info["file_kept_other_refs"] = other
    return info


def attach_attachments(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """entries 의 각 dict 에 'local_attachments' 필드 추가 (annotations 와 같은 패턴).

    부착되는 형태: list of {id, file_path, original_filename, attached_at, note, mime_type}.
    파일 없는 entry 는 빈 리스트.
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
        # 컴팩트 형태 (전체 db row 다 노출 X)
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


def detect_mime(path: Path) -> str | None:
    mt, _ = mimetypes.guess_type(str(path))
    return mt
