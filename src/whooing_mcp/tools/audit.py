"""whooing_audit_recent_ai_entries — DESIGN v2 §6.3.

LLM 이 사용자 위임으로 후잉에 입력한 거래만 골라본다. 공식 MCP 의 add_entry
도구에는 우리가 hook 을 못 거므로, **컨벤션** 으로 해결: LLM 에 "memo 첫
단어로 `[ai]` 를 붙여라" 라고 README + 도구 description 으로 안내한다.
"""

from __future__ import annotations

from typing import Any

from whooing_mcp.annotations import attach_annotations
from whooing_mcp.attachments import attach_attachments
from whooing_mcp.client import WhooingClient
from whooing_mcp.dates import days_ago_yyyymmdd, today_yyyymmdd
from whooing_mcp.models import ToolError

DEFAULT_MARKER = "[ai]"


async def audit_recent_ai_entries(
    client: WhooingClient,
    section_id: str,
    days: int = 7,
    marker: str = DEFAULT_MARKER,
) -> dict[str, Any]:
    """최근 `days` 일의 후잉 거래 중 memo / item 이 `marker` 로 시작하는 것만 반환."""
    if days < 1 or days > 366:
        raise ToolError("USER_INPUT", f"days 는 1~366 범위여야 합니다 (받음: {days}).")
    if not marker:
        raise ToolError("USER_INPUT", "marker 는 비어있을 수 없습니다.")

    start = days_ago_yyyymmdd(days - 1)
    end = today_yyyymmdd()

    raw = await client.list_entries(
        section_id=section_id,
        start_date=start,
        end_date=end,
    )

    marker_lower = marker.lower()
    matched: list[dict[str, Any]] = []
    for e in raw:
        memo = (e.get("memo") or "").strip().lower()
        item = (e.get("item") or "").strip().lower()
        if memo.startswith(marker_lower) or item.startswith(marker_lower):
            matched.append(e)

    matched.sort(key=lambda e: e.get("entry_date") or "", reverse=True)
    matched = attach_annotations(matched)  # 로컬 note + hashtags 부착
    matched = attach_attachments(matched)  # 로컬 첨부파일 부착

    return {
        "entries": matched,
        "total": len(matched),
        "marker_used": marker,
        "section_id": section_id,
        "date_range": {"start": start, "end": end},
        "scanned_total": len(raw),
        "note": (
            f"LLM 이 입력한 거래로 추적되려면, 공식 MCP 의 add_entry 호출 시 "
            f"memo 첫 단어로 '{marker}' 를 붙이세요. "
            "예: memo='[ai] 사용자 음성 위임'."
        ),
    }
