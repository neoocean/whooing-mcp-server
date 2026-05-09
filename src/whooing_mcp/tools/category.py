"""whooing_suggest_category — DESIGN v2 §14 P2.

사용자의 과거 거래에서 "merchant → l_account" 매핑을 학습해 새 거래의
카테고리를 추천한다. 학습 = 단순 유사도 가중 vote (ML 아님).

read-only — 추천만 한다. 실제 add_entry 는 LLM 이 사용자 확인 후 공식
MCP 로 처리.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from rapidfuzz import fuzz

from whooing_mcp.client import WhooingClient
from whooing_mcp.dates import days_ago_yyyymmdd, today_yyyymmdd
from whooing_mcp.models import ToolError


async def suggest_category(
    client: WhooingClient,
    merchant: str,
    section_id: str,
    lookback_days: int = 180,
    min_similarity: float = 0.50,
    top_k: int = 3,
) -> dict[str, Any]:
    # ---- 입력 검증 ----
    if not isinstance(merchant, str) or not merchant.strip():
        raise ToolError("USER_INPUT", "merchant 가 비어있습니다.")
    merchant = merchant.strip()

    if lookback_days < 1 or lookback_days > 730:
        raise ToolError(
            "USER_INPUT",
            f"lookback_days 는 1~730 (받음: {lookback_days})",
        )
    if not (0.0 <= min_similarity <= 1.0):
        raise ToolError(
            "USER_INPUT",
            f"min_similarity 는 0.0~1.0 (받음: {min_similarity})",
        )
    if top_k < 1 or top_k > 20:
        raise ToolError("USER_INPUT", f"top_k 는 1~20 (받음: {top_k})")

    start = days_ago_yyyymmdd(lookback_days - 1)
    end = today_yyyymmdd()

    entries = await client.list_entries(
        section_id=section_id,
        start_date=start,
        end_date=end,
    )

    suggestions = _vote(entries, merchant, min_similarity, top_k)

    return {
        "suggested": suggestions,
        "merchant_searched": merchant,
        "section_id": section_id,
        "lookback_days": lookback_days,
        "scanned_total": len(entries),
        "match_count": sum(s["evidence_count"] for s in suggestions),
        "params": {
            "min_similarity": min_similarity,
            "top_k": top_k,
        },
        "note": _build_note(suggestions, len(entries)),
    }


def _vote(
    entries: list[dict[str, Any]],
    merchant: str,
    min_similarity: float,
    top_k: int,
) -> list[dict[str, Any]]:
    """과거 거래 중 merchant 와 유사한 item 의 l_account 를 가중 vote."""
    threshold = min_similarity * 100.0

    # l_account → {weight_sum, count, evidence: [...]}
    votes: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"weight_sum": 0.0, "count": 0, "evidence": []}
    )

    for e in entries:
        item = (e.get("item") or "").strip()
        if not item:
            continue
        l_account = (e.get("l_account") or "").strip()
        if not l_account:
            continue

        # token_set_ratio: '스타벅스 강남점' ↔ '스타벅스 역삼점' 강하게 매칭
        sim = float(fuzz.token_set_ratio(merchant, item))
        if sim < threshold:
            continue

        v = votes[l_account]
        v["weight_sum"] += sim
        v["count"] += 1
        v["evidence"].append({
            "entry_id": e.get("entry_id"),
            "item": item,
            "entry_date": e.get("entry_date"),
            "money": e.get("money"),
            "similarity": round(sim / 100.0, 3),
        })

    if not votes:
        return []

    total_weight = sum(v["weight_sum"] for v in votes.values())
    if total_weight == 0:
        return []

    suggestions = []
    for l_account, v in votes.items():
        # evidence 는 유사도 desc, 상위 3개만 (LLM 컨텍스트 절약)
        v["evidence"].sort(key=lambda x: x["similarity"], reverse=True)
        suggestions.append({
            "l_account": l_account,
            "confidence": round(v["weight_sum"] / total_weight, 3),
            "evidence_count": v["count"],
            "evidence": v["evidence"][:3],
        })

    suggestions.sort(key=lambda s: s["confidence"], reverse=True)
    return suggestions[:top_k]


def _build_note(suggestions: list[dict[str, Any]], scanned_total: int) -> str:
    if not suggestions:
        if scanned_total == 0:
            return (
                "lookback 기간 내 거래가 없어 학습 데이터가 없습니다. "
                "LLM 이 사용자에게 카테고리를 직접 물어보세요."
            )
        return (
            f"학습 데이터 {scanned_total}건은 있으나 merchant 와 충분히 유사한 "
            f"과거 거래 없음. min_similarity 를 낮추거나 LLM 이 직접 추정."
        )
    top = suggestions[0]
    return (
        f"가장 유력: l_account='{top['l_account']}' (confidence={top['confidence']}, "
        f"근거 {top['evidence_count']}건). "
        "LLM 은 사용자에게 후보 + evidence 를 보여주고 확인 후 "
        "공식 MCP 의 add_entry 호출하세요."
    )
