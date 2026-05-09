"""Pending queue tools — DESIGN §14 P2.

후잉 공식 자동입력 대기열은 외부 API 미노출. 본 wrapper 가 별도 SQLite
큐 운영. 4 도구:

  whooing_enqueue_pending  — 텍스트/parsed dict 를 큐에 넣음
  whooing_list_pending     — 큐 조회
  whooing_confirm_pending  — 후잉에 입력 완료 후 큐 삭제 (실 add_entry 는 LLM 책임)
  whooing_dismiss_pending  — 입력 안 함, 큐 삭제 (의미 구분만)
"""

from __future__ import annotations

from typing import Any

from whooing_mcp.models import ToolError
from whooing_mcp.p4_sync import sync_db_to_p4
from whooing_mcp.queue import count, delete, insert, list_items, open_db


async def enqueue_pending(
    text: str | None = None,
    parsed: dict[str, Any] | None = None,
    source: str = "manual",
    issuer: str | None = None,
    section_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    if not text and not parsed:
        raise ToolError(
            "USER_INPUT",
            "text 또는 parsed 중 최소 하나는 필요합니다.",
        )
    if source not in ("manual", "sms", "email"):
        raise ToolError(
            "USER_INPUT",
            f"source 는 'manual'|'sms'|'email' 중 하나 (받음: {source!r})",
        )

    with open_db() as conn:
        result = insert(
            conn,
            source=source,
            raw_text=text,
            parsed=parsed,
            issuer=issuer,
            section_id=section_id,
            note=note,
        )
        result["queue_total"] = count(conn)

    result["note"] = (
        "큐에 저장됨. whooing_list_pending 으로 조회, 처리 후 "
        "whooing_confirm_pending(pending_id) 또는 무시 시 "
        "whooing_dismiss_pending(pending_id)."
    )
    result["p4_sync"] = sync_db_to_p4(
        f"queue.enqueue (id={result['pending_id']}, source={source})"
    )
    return result


async def list_pending(
    source: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    if source is not None and source not in ("manual", "sms", "email"):
        raise ToolError(
            "USER_INPUT",
            f"source 필터는 'manual'|'sms'|'email' (받음: {source!r})",
        )
    if limit < 1 or limit > 1000:
        raise ToolError("USER_INPUT", f"limit 는 1~1000 (받음: {limit})")

    with open_db() as conn:
        items = list_items(conn, source=source, since=since, limit=limit)
        total = count(conn)

    return {
        "items": items,
        "returned": len(items),
        "total_in_queue": total,
        "filters": {"source": source, "since": since, "limit": limit},
        "note": (
            "각 item 의 'parsed' 필드를 LLM 이 사용자에게 보여주고 카테고리 "
            "등을 결정. 공식 MCP 의 add_entry 호출 후 whooing_confirm_pending "
            "으로 큐 정리."
        ),
    }


async def confirm_pending(
    pending_id: int,
    note: str | None = None,
) -> dict[str, Any]:
    """후잉에 add_entry 가 완료된 후 큐에서 제거. 실제 add_entry 는 LLM 책임."""
    if not isinstance(pending_id, int) or pending_id < 1:
        raise ToolError("USER_INPUT", f"pending_id 는 양의 정수 (받음: {pending_id!r})")

    with open_db() as conn:
        deleted = delete(conn, pending_id)
        remaining = count(conn)

    if deleted is None:
        raise ToolError(
            "USER_INPUT",
            f"pending_id={pending_id} 가 큐에 없습니다 (이미 삭제됐거나 잘못된 ID).",
        )

    sync = sync_db_to_p4(f"queue.confirm (id={pending_id})")
    return {
        "removed": True,
        "deleted_item": deleted,
        "remaining_in_queue": remaining,
        "outcome": "confirmed",
        "user_note": note,
        "important": (
            "본 도구는 우리 자체 큐에서만 삭제합니다. 후잉 가계부에 실제 "
            "입력이 됐는지는 별도로 공식 MCP 의 add_entry 호출이 성공했음을 "
            "LLM 이 확인해야 합니다."
        ),
        "p4_sync": sync,
    }


async def dismiss_pending(
    pending_id: int,
    reason: str | None = None,
) -> dict[str, Any]:
    """큐에서 제거 — 후잉에 입력하지 않을 항목."""
    if not isinstance(pending_id, int) or pending_id < 1:
        raise ToolError("USER_INPUT", f"pending_id 는 양의 정수 (받음: {pending_id!r})")

    with open_db() as conn:
        deleted = delete(conn, pending_id)
        remaining = count(conn)

    if deleted is None:
        raise ToolError(
            "USER_INPUT",
            f"pending_id={pending_id} 가 큐에 없습니다.",
        )

    sync = sync_db_to_p4(f"queue.dismiss (id={pending_id})")
    return {
        "removed": True,
        "deleted_item": deleted,
        "remaining_in_queue": remaining,
        "outcome": "dismissed",
        "reason": reason,
        "p4_sync": sync,
    }
