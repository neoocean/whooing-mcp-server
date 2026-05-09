"""whooing_find_duplicates — DESIGN v2 §6.2.

같은 금액 + 유사 item + ±tolerance_days 안 거래쌍을 중복 후보로 반환.
read-only — 실제 삭제는 사용자가 공식 MCP 의 delete_entry 로 처리.

알고리즘은 단순한 O(n + Σg²) (g 는 같은 금액 버킷 크기). 실 가계부의
같은-금액 버킷은 보통 작아서 (≤10) 실용적.
"""

from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz

from whooing_mcp.client import WhooingClient
from whooing_mcp.dates import date_diff_days, parse_yyyymmdd
from whooing_mcp.models import ToolError


async def find_duplicates(
    client: WhooingClient,
    section_id: str,
    start_date: str,
    end_date: str,
    tolerance_days: int = 1,
    min_similarity: float = 0.85,
) -> dict[str, Any]:
    # ---- 입력 검증 ----
    try:
        parse_yyyymmdd(start_date)
        parse_yyyymmdd(end_date)
    except ValueError as ex:
        raise ToolError("USER_INPUT", str(ex))
    if start_date > end_date:
        raise ToolError(
            "USER_INPUT",
            f"start_date({start_date}) > end_date({end_date})",
        )
    if tolerance_days < 0 or tolerance_days > 30:
        raise ToolError(
            "USER_INPUT",
            f"tolerance_days 는 0~30 범위여야 합니다 (받음: {tolerance_days}).",
        )
    if not (0.0 <= min_similarity <= 1.0):
        raise ToolError(
            "USER_INPUT",
            f"min_similarity 는 0.0~1.0 (받음: {min_similarity}).",
        )

    entries = await client.list_entries(
        section_id=section_id,
        start_date=start_date,
        end_date=end_date,
    )

    pairs = _find_pairs(entries, tolerance_days, min_similarity)

    return {
        "pairs": pairs,
        "total_checked": len(entries),
        "section_id": section_id,
        "date_range": {"start": start_date, "end": end_date},
        "params": {
            "tolerance_days": tolerance_days,
            "min_similarity": min_similarity,
        },
        "note": (
            "중복 _후보_ 만 반환합니다. 실제 삭제는 entry_id 를 확인 후 "
            "공식 MCP 의 delete_entry 로 처리하세요."
        ),
    }


def _find_pairs(
    entries: list[dict[str, Any]],
    tolerance_days: int,
    min_similarity: float,
) -> list[dict[str, Any]]:
    """입력은 raw entries (dict 리스트). 출력은 (entry_a, entry_b, why) dict 리스트."""
    # money 기준 버킷팅 — 같은 금액인 거래만 비교 후보
    by_money: dict[int, list[dict[str, Any]]] = {}
    for e in entries:
        money = e.get("money")
        if money is None:
            continue
        # money 가 str 로 올 수 있음 (방어)
        try:
            key = int(money)
        except (TypeError, ValueError):
            continue
        by_money.setdefault(key, []).append(e)

    threshold = min_similarity * 100.0
    pairs: list[dict[str, Any]] = []

    for money, group in by_money.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            a = group[i]
            for j in range(i + 1, len(group)):
                b = group[j]
                why = _compare_pair(a, b, money, tolerance_days, threshold)
                if why is not None:
                    pairs.append({"entry_a": a, "entry_b": b, "why": why})

    # date desc 정렬 (entry_a 의 date 기준)
    pairs.sort(key=lambda p: p["entry_a"].get("entry_date") or "", reverse=True)
    return pairs


def _compare_pair(
    a: dict[str, Any],
    b: dict[str, Any],
    money: int,
    tolerance_days: int,
    threshold: float,
) -> list[str] | None:
    """매칭이면 why 리스트, 아니면 None."""
    da = a.get("entry_date")
    db = b.get("entry_date")
    if not (da and db):
        return None
    try:
        days = date_diff_days(da, db)
    except (ValueError, TypeError):
        return None
    if days > tolerance_days:
        return None

    item_a = (a.get("item") or "").strip()
    item_b = (b.get("item") or "").strip()
    if not item_a or not item_b:
        # item 이 비어있으면 매칭 신뢰도 낮음 — money + date 로만 보고하되 sim=0
        sim = 0.0
    else:
        sim = float(fuzz.ratio(item_a, item_b))

    if sim < threshold:
        return None

    return [
        f"same money: {money}",
        f"item similarity: {sim:.0f}/100",
        f"days apart: {days}",
    ]
