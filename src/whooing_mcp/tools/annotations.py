"""거래 annotation (note + hashtags) 5 도구.

후잉 자체 memo 가 부족한 영역 (한 줄 / 해시태그 검색 X) 을 로컬에서 보완.
"""

from __future__ import annotations

from typing import Any

from whooing_mcp.annotations import (
    attach_annotations,
    delete_annotation,
    find_entry_ids_by_hashtag,
    get_annotations,
    list_all_hashtags,
    parse_hashtag_input,
    upsert_annotation,
)
from whooing_mcp.client import WhooingClient
from whooing_mcp.dates import days_ago_yyyymmdd, today_yyyymmdd
from whooing_mcp.models import ToolError
from whooing_mcp.queue import open_db


# ---- set_entry_note -----------------------------------------------------


async def set_entry_note(
    entry_id: str,
    note: str | None = None,
    hashtags: str | list[str] | None = None,
    section_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise ToolError("USER_INPUT", "entry_id 가 비어있습니다.")
    entry_id = entry_id.strip()

    if note is None and hashtags is None:
        raise ToolError(
            "USER_INPUT",
            "note 또는 hashtags 중 하나는 제공해야 합니다.",
        )

    try:
        normalized_tags = parse_hashtag_input(hashtags) if hashtags is not None else None
    except ValueError as ex:
        raise ToolError("USER_INPUT", f"hashtags 형식 오류: {ex}")

    with open_db() as conn:
        result = upsert_annotation(
            conn,
            entry_id=entry_id,
            note=note,
            hashtags=normalized_tags,
            section_id=section_id,
        )

    return {
        "annotation": result,
        "note": (
            "후잉 거래에 _로컬_ annotation 이 저장되었습니다. 후잉 서버의 memo "
            "는 변경되지 않습니다 — 본 wrapper 의 SQLite 큐에만 저장됩니다."
        ),
    }


# ---- get_entry_annotations ---------------------------------------------


async def get_entry_annotations(
    entry_ids: str | list[str],
) -> dict[str, Any]:
    if isinstance(entry_ids, str):
        entry_ids = [entry_ids]
    if not isinstance(entry_ids, list) or not entry_ids:
        raise ToolError("USER_INPUT", "entry_ids 는 비어있지 않은 list[str].")

    cleaned = [str(eid).strip() for eid in entry_ids if str(eid).strip()]
    if not cleaned:
        raise ToolError("USER_INPUT", "유효한 entry_id 없음.")

    with open_db() as conn:
        result = get_annotations(conn, cleaned)

    return {
        "annotations": result,
        "found_count": len(result),
        "queried_count": len(cleaned),
    }


# ---- remove_entry_note --------------------------------------------------


async def remove_entry_note(entry_id: str) -> dict[str, Any]:
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise ToolError("USER_INPUT", "entry_id 가 비어있습니다.")
    entry_id = entry_id.strip()

    with open_db() as conn:
        removed = delete_annotation(conn, entry_id)

    if not removed:
        return {
            "removed": False,
            "entry_id": entry_id,
            "note": "해당 entry_id 에 로컬 annotation 이 없었습니다.",
        }

    return {
        "removed": True,
        "entry_id": entry_id,
        "note": "후잉 서버의 entry 자체는 영향 없음. 로컬 annotation 만 삭제.",
    }


# ---- list_hashtags ------------------------------------------------------


async def list_hashtags(prefix: str | None = None) -> dict[str, Any]:
    with open_db() as conn:
        tags = list_all_hashtags(conn, prefix=prefix)

    return {
        "hashtags": tags,
        "total_unique": len(tags),
        "prefix_filter": prefix,
    }


# ---- find_entries_by_hashtag (역방향 조회) -----------------------------


async def find_entries_by_hashtag(
    client: WhooingClient,
    hashtag: str,
    section_id: str,
    lookback_days: int = 365,
) -> dict[str, Any]:
    """해시태그 → 매칭 entry_id 들 → 후잉에서 fetch → annotations 부착."""
    if not isinstance(hashtag, str) or not hashtag.strip():
        raise ToolError("USER_INPUT", "hashtag 가 비어있습니다.")
    if lookback_days < 1 or lookback_days > 730:
        raise ToolError("USER_INPUT", f"lookback_days 1~730 (받음: {lookback_days})")

    try:
        from whooing_mcp.annotations import normalize_hashtag
        norm_tag = normalize_hashtag(hashtag)
    except ValueError as ex:
        raise ToolError("USER_INPUT", str(ex))

    with open_db() as conn:
        matched_ids = set(find_entry_ids_by_hashtag(conn, norm_tag))

    if not matched_ids:
        return {
            "entries": [],
            "total": 0,
            "hashtag_searched": norm_tag,
            "note": (
                f"해시태그 '{norm_tag}' 가 붙은 거래가 로컬에 없습니다. "
                "whooing_set_entry_note 로 먼저 태그를 붙이세요."
            ),
        }

    # 후잉에서 lookback 범위 entries fetch → matched_ids 로 필터
    start = days_ago_yyyymmdd(lookback_days - 1)
    end = today_yyyymmdd()

    raw = await client.list_entries(
        section_id=section_id,
        start_date=start,
        end_date=end,
    )

    matched_entries = [e for e in raw if e.get("entry_id") in matched_ids]

    # 후잉에 없는 (오래되어 lookback 밖인) entry_id 도 보고
    fetched_ids = {e.get("entry_id") for e in matched_entries}
    missing_in_remote = sorted(matched_ids - fetched_ids)

    enriched = attach_annotations(matched_entries)
    enriched.sort(key=lambda e: e.get("entry_date") or "", reverse=True)

    return {
        "entries": enriched,
        "total": len(enriched),
        "hashtag_searched": norm_tag,
        "section_id": section_id,
        "date_range": {"start": start, "end": end},
        "missing_in_remote_count": len(missing_in_remote),
        "missing_in_remote_ids": missing_in_remote[:20],  # 컨텍스트 절약
        "note": (
            f"로컬에 '{norm_tag}' 태그 {len(matched_ids)}건, 그 중 "
            f"lookback {lookback_days}일 안 후잉에 살아있는 것 {len(enriched)}건. "
            "missing_in_remote_ids 는 후잉에서 삭제됐거나 범위 밖. lookback_days "
            "를 늘려보거나 로컬 annotation 정리 (whooing_remove_entry_note)."
        ),
    }
