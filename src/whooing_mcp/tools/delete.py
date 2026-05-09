"""whooing_delete_entries — 공식 MCP 통한 거래 삭제 자동화.

본 wrapper 는 후잉 REST 를 직접 두드리지 않는 read-only 정책이지만,
공식 MCP 의 entries-delete 를 chained-call 하는 형태는 정책 일관성 유지.
재무 데이터 영구 삭제 — confirm=True 가드.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from typing import Any

from whooing_mcp.dates import KST
from whooing_mcp.models import ToolError
from whooing_mcp.official_mcp import OfficialMcpClient, OfficialMcpError
from whooing_mcp.queue import open_db


# 분당 18 self-throttle (서버 한도 20 — buffer)
_RPM_CAP = 18


def _now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


async def delete_entries(
    entry_ids: str | list[str],
    section_id: str,
    confirm: bool = False,
    update_import_log: bool = True,
    official_url: str | None = None,
) -> dict[str, Any]:
    # ---- 입력 검증 + 안전 가드 ----
    if not confirm:
        raise ToolError(
            "USER_INPUT",
            "재무 데이터 영구 삭제 도구입니다. 'confirm=True' 명시 필요. "
            "공식 MCP 의 entries-delete 를 호출해 후잉 ledger 에서 영구 삭제됩니다.",
        )

    if isinstance(entry_ids, str):
        entry_ids = [entry_ids]
    if not isinstance(entry_ids, list) or not entry_ids:
        raise ToolError("USER_INPUT", "entry_ids 는 비어있지 않은 list[str].")

    cleaned = [str(e).strip() for e in entry_ids if str(e).strip()]
    if not cleaned:
        raise ToolError("USER_INPUT", "유효한 entry_id 없음.")

    if not isinstance(section_id, str) or not section_id.strip():
        raise ToolError("USER_INPUT", "section_id 가 비어있습니다.")

    token = os.getenv("WHOOING_AI_TOKEN", "").strip()
    if not token:
        raise ToolError("AUTH", "WHOOING_AI_TOKEN 미설정.")

    client_kwargs: dict[str, Any] = {"token": token}
    if official_url:
        client_kwargs["base_url"] = official_url
    client = OfficialMcpClient(**client_kwargs)

    deleted: list[str] = []
    failed: list[dict[str, Any]] = []
    rate_window: list[float] = []

    for eid in cleaned:
        # client-side throttle (분당 18)
        now_t = time.monotonic()
        rate_window = [t for t in rate_window if now_t - t < 60]
        if len(rate_window) >= _RPM_CAP:
            wait = 60 - (now_t - rate_window[0]) + 0.5
            await asyncio.sleep(wait)
            rate_window = []

        try:
            result = await client.call_tool(
                "entries-delete",
                {"section_id": section_id, "entry_id": eid},
            )
            rate_window.append(time.monotonic())
            deleted.append(eid)
        except OfficialMcpError as ex:
            rate_window.append(time.monotonic())
            failed.append({"entry_id": eid, "error": str(ex), "code": ex.code})

    # ---- import_log 업데이트 (옵션) ----
    log_updates = {"updated": 0, "not_found": 0}
    if update_import_log and deleted:
        log_updates = _update_import_log(deleted)

    return {
        "summary": {
            "requested": len(cleaned),
            "deleted_count": len(deleted),
            "failed_count": len(failed),
        },
        "deleted": deleted,
        "failed": failed,
        "log_updates": log_updates,
        "via": "official_mcp/entries-delete",
        "note": (
            "공식 MCP 통해 ledger 영구 삭제 완료. statement_import_log 도 "
            "동기화 (해당 entry_id 들의 status='deleted'). P4 sync 는 다음 "
            "modifying 도구 호출 시 자동."
            if deleted else
            "삭제된 entry 없음 — 모두 실패 또는 빈 입력."
        ),
    }


def _update_import_log(deleted_eids: list[str]) -> dict[str, int]:
    """statement_import_log 에서 해당 entry_id 들의 status 를 'deleted' 로."""
    updated = 0
    not_found = 0
    suffix = " | deleted via whooing_delete_entries at " + _now_iso()
    with open_db() as conn:
        for eid in deleted_eids:
            cur = conn.execute(
                """UPDATE statement_import_log
                   SET status = 'deleted', notes = COALESCE(notes, '') || ?
                   WHERE whooing_entry_id = ?""",
                (suffix, eid),
            )
            if cur.rowcount > 0:
                updated += 1
            else:
                not_found += 1
    return {"updated": updated, "not_found": not_found}
