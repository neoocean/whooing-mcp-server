"""거래 ↔ 첨부파일 도구 3종.

후잉이 entry-attachment 미지원이라 본 wrapper 가 별개 SQLite layer 에서 보관.
파일은 ./attachments/files/YYYY/YYYY-MM-DD/ 에 복사 (또는 dedup 으로 reuse).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from whooing_mcp.attachments import (
    copy_to_attachments,
    delete_attachment,
    detect_mime,
    list_attachments_for,
    upsert_attachment,
)
from whooing_mcp.models import ToolError
from whooing_mcp.p4_sync import sync_db_to_p4, sync_paths_to_p4
from whooing_mcp.queue import default_queue_path, open_db


# ---- attach_file_to_entry ---------------------------------------------


async def attach_file_to_entry(
    entry_id: str,
    file_path: str,
    section_id: str | None = None,
    note: str | None = None,
    attach_date: str | None = None,
    copy: bool = True,
) -> dict[str, Any]:
    """파일을 entry 에 첨부.

    Args:
      entry_id: 후잉 entry_id (str — 후잉이 정수지만 일관성 위해 str)
      file_path: 절대경로
      section_id: 컨텍스트 (없으면 None — db 에 NULL)
      note: 사용자 메모 (예: "Atlassian 인보이스, 결제 영수증 첨부")
      attach_date: YYYY-MM-DD (디렉터리 분류용, default today)
      copy: True 면 attachments/files/YYYY/YYYY-MM-DD/ 로 복사. False 면 원본 경로 그대로
            db 에 기록 (외부 사람과 동기 안 됨 — cross-machine 권장 X).
    """
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise ToolError("USER_INPUT", "entry_id 가 비어있습니다.")
    entry_id = entry_id.strip()

    if not isinstance(file_path, str) or not file_path.strip():
        raise ToolError("USER_INPUT", "file_path 가 비어있습니다.")
    src = Path(file_path).expanduser()
    if not src.is_absolute():
        raise ToolError("USER_INPUT", f"file_path 는 절대 경로여야 합니다: {file_path!r}")
    if not src.exists():
        raise ToolError("USER_INPUT", f"파일이 없습니다: {src}")
    if not src.is_file():
        raise ToolError("USER_INPUT", f"디렉터리는 첨부할 수 없습니다: {src}")

    if attach_date is not None and not _is_valid_date(attach_date):
        raise ToolError("USER_INPUT", f"attach_date 는 YYYY-MM-DD 형식: {attach_date!r}")

    # ---- 복사 (또는 원본 path 그대로) ----
    if copy:
        try:
            copied_path, sha256, size = copy_to_attachments(src, attach_date=attach_date)
        except (FileNotFoundError, ValueError) as e:
            raise ToolError("USER_INPUT", str(e))
        # 프로젝트 루트 기준 relative path
        project_root = Path(__file__).resolve().parents[3]
        try:
            rel_path = str(copied_path.relative_to(project_root))
        except ValueError:
            rel_path = str(copied_path)
        original_filename = src.name
        original_path = str(src)
    else:
        rel_path = str(src)
        from whooing_mcp.attachments import _sha256_of_file
        sha256 = _sha256_of_file(src)
        size = src.stat().st_size
        original_filename = src.name
        original_path = str(src)

    mime_type = detect_mime(src)

    with open_db() as conn:
        attachment_row = upsert_attachment(
            conn,
            entry_id=entry_id,
            section_id=section_id,
            file_path=rel_path,
            original_path=original_path,
            original_filename=original_filename,
            file_size_bytes=size,
            file_sha256=sha256,
            mime_type=mime_type,
            note=note,
        )

    # db + (옵션) 새 첨부파일 모두 한 CL 로 sync.
    # rel_path 는 위에서 'attachments/files/...' (프로젝트 루트 상대) 또는
    # 원본 absolute path 로 세팅됨. p4 add/edit 에는 absolute 가 필요하므로 변환.
    sync_paths = [default_queue_path()]
    if copy:
        rel_p = Path(rel_path)
        if rel_p.is_absolute():
            sync_paths.append(rel_p)
        else:
            project_root = Path(__file__).resolve().parents[3]
            sync_paths.append(project_root / rel_path)

    sync = sync_paths_to_p4(
        f"attachment.attach (entry={entry_id}, file={original_filename})",
        paths=sync_paths,
    )
    return {
        "attachment": {
            "id": attachment_row["id"],
            "entry_id": entry_id,
            "file_path": rel_path,
            "original_filename": original_filename,
            "size_bytes": size,
            "mime_type": mime_type,
            "note": note,
            "attached_at": attachment_row["attached_at"],
        },
        "copied": copy,
        "deduped": (rel_path != str(src) and copy and attachment_row.get("file_sha256") == sha256),
        "p4_sync": sync,
        "note": (
            "후잉 ledger 의 entry 자체는 변경되지 않음. 본 wrapper 의 로컬 SQLite "
            "+ 디스크에만 보관. db 는 자동 P4 sync 됐고, 첨부파일 자체는 별도 "
            "수동 `p4 reconcile + submit` 또는 사용자 정기 백업 권장."
        ),
    }


# ---- list_entry_attachments -------------------------------------------


async def list_entry_attachments(
    entry_ids: str | list[str],
) -> dict[str, Any]:
    if isinstance(entry_ids, str):
        entry_ids = [entry_ids]
    if not isinstance(entry_ids, list) or not entry_ids:
        raise ToolError("USER_INPUT", "entry_ids 는 비어있지 않은 list[str].")
    cleaned = [str(e).strip() for e in entry_ids if str(e).strip()]
    if not cleaned:
        raise ToolError("USER_INPUT", "유효한 entry_id 없음.")

    with open_db() as conn:
        amap = list_attachments_for(conn, cleaned)

    # 컴팩트 형태로 — 전체 db row 노출 X
    compact = {}
    for eid, atts in amap.items():
        compact[eid] = [
            {
                "id": a["id"],
                "file_path": a["file_path"],
                "original_filename": a["original_filename"],
                "size_bytes": a["file_size_bytes"],
                "mime_type": a["mime_type"],
                "note": a["note"],
                "attached_at": a["attached_at"],
            }
            for a in atts
        ]
    return {
        "attachments_by_entry": compact,
        "queried_count": len(cleaned),
        "found_entries": len(compact),
        "total_attachments": sum(len(v) for v in compact.values()),
    }


# ---- remove_attachment ----------------------------------------------


async def remove_attachment(
    attachment_id: int,
    delete_file: bool = True,
) -> dict[str, Any]:
    if not isinstance(attachment_id, int) or attachment_id < 1:
        raise ToolError("USER_INPUT", f"attachment_id 는 양의 정수: {attachment_id!r}")

    with open_db() as conn:
        deleted = delete_attachment(conn, attachment_id, delete_file=delete_file)

    if deleted is None:
        raise ToolError(
            "USER_INPUT",
            f"attachment_id={attachment_id} 가 db 에 없음 (이미 삭제됐거나 잘못된 ID).",
        )

    sync = sync_db_to_p4(
        f"attachment.remove (id={attachment_id}, entry={deleted['entry_id']})"
    )
    return {
        "removed": True,
        "deleted_row": {
            "id": deleted["id"],
            "entry_id": deleted["entry_id"],
            "file_path": deleted["file_path"],
            "original_filename": deleted["original_filename"],
        },
        "file_deleted": deleted.get("file_deleted", False),
        "file_kept_other_refs": deleted.get("file_kept_other_refs", 0),
        "file_delete_error": deleted.get("file_delete_error"),
        "p4_sync": sync,
        "note": (
            "row 삭제됨. delete_file=True 면 디스크 파일도 제거 (단, 같은 sha256 의 "
            "다른 row 가 남아있으면 파일은 보존). 후잉 ledger 의 entry 자체는 영향 X."
        ),
    }


def _is_valid_date(s: str) -> bool:
    from datetime import datetime
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False
