"""whooing_monthly_close — DESIGN v2 §14 P2.

월말 정산 한 번 호출로 끝내는 합성 도구. audit + find_duplicates +
(선택) reconcile_csv/pdf + 월간 합계를 모두 묶어 반환한다. LLM 이 결과
보고 사용자와 정리.

신규 API 호출 없음 — 기존 client.list_entries 한두 번 + tool 모듈들 호출.
"""

from __future__ import annotations

import calendar
import os
from collections import defaultdict
from typing import Any

from whooing_mcp.client import WhooingClient
from whooing_mcp.dates import parse_yyyymmdd
from whooing_mcp.models import ToolError
from whooing_mcp.tools.audit import audit_recent_ai_entries
from whooing_mcp.tools.dedup import find_duplicates


def _yyyymm_to_range(yyyymm: str) -> tuple[str, str]:
    """'202604' → ('20260401', '20260430')."""
    if not isinstance(yyyymm, str) or len(yyyymm) != 6 or not yyyymm.isdigit():
        raise ToolError("USER_INPUT", f"yyyymm 은 6자리 숫자 (받음: {yyyymm!r})")
    year = int(yyyymm[:4])
    month = int(yyyymm[4:])
    if not (1 <= month <= 12):
        raise ToolError("USER_INPUT", f"month 는 1~12 (받음: {month})")
    last_day = calendar.monthrange(year, month)[1]
    start = f"{year:04d}{month:02d}01"
    end = f"{year:04d}{month:02d}{last_day:02d}"
    return start, end


async def monthly_close(
    client: WhooingClient,
    yyyymm: str,
    section_id: str,
    csv_path: str | None = None,
    pdf_path: str | None = None,
    statement_issuer: str = "auto",
    duplicate_tolerance_days: int = 1,
    duplicate_min_similarity: float = 0.85,
    audit_marker: str = "[ai]",
) -> dict[str, Any]:
    start, end = _yyyymm_to_range(yyyymm)

    # ---- 1. 월간 entries fetch (한 번) ----
    entries = await client.list_entries(
        section_id=section_id,
        start_date=start,
        end_date=end,
    )

    # ---- 2. summary 통계 ----
    summary = _build_summary(entries)

    # ---- 3. audit (LLM 입력 거래만) ----
    # 본 도구는 마커 매칭만 직접 수행 (audit_recent_ai_entries 의 days 인터페이스
    # 와 다른 yyyymm 범위 — 직접 필터링).
    ai_matched = _filter_by_marker(entries, audit_marker)
    ai_entries_summary = {
        "count": len(ai_matched),
        "marker_used": audit_marker,
        "top_5": ai_matched[:5],
    }

    # ---- 4. duplicates (월 범위) ----
    dup_result = await find_duplicates(
        client,
        section_id=section_id,
        start_date=start,
        end_date=end,
        tolerance_days=duplicate_tolerance_days,
        min_similarity=duplicate_min_similarity,
    )
    duplicates_summary = {
        "pairs_count": len(dup_result["pairs"]),
        "pairs": dup_result["pairs"][:10],  # 상위 10개 (LLM 컨텍스트 절약)
    }

    # ---- 5. reconcile (선택) ----
    reconcile_summary = None
    if csv_path or pdf_path:
        reconcile_summary = await _do_reconcile(
            client,
            csv_path=csv_path,
            pdf_path=pdf_path,
            section_id=section_id,
            issuer=statement_issuer,
            start=start,
            end=end,
        )

    # ---- 6. next_actions 가이드 ----
    next_actions = _build_next_actions(
        ai_count=ai_entries_summary["count"],
        dup_count=duplicates_summary["pairs_count"],
        reconcile=reconcile_summary,
    )

    return {
        "month": yyyymm,
        "section_id": section_id,
        "date_range": {"start": start, "end": end},
        "summary": summary,
        "ai_entries": ai_entries_summary,
        "duplicates": duplicates_summary,
        "reconcile": reconcile_summary,
        "next_actions": next_actions,
        "note": (
            "본 도구는 신규 API 호출 없이 기존 도구들을 묶은 합성 결과입니다. "
            "missing_in_whooing 항목 입력 / 중복 삭제 / [ai] 마커 항목 검토는 "
            "각각 공식 MCP 의 add_entry / delete_entry / 사용자 직접 처리 권장."
        ),
    }


def _build_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """entries 의 거시 통계."""
    total_money_sum = 0
    by_l_account: dict[str, int] = defaultdict(int)
    for e in entries:
        money = e.get("money")
        try:
            m = int(money) if money is not None else 0
        except (TypeError, ValueError):
            m = 0
        total_money_sum += m
        l = (e.get("l_account") or "").strip() or "<unknown>"
        by_l_account[l] += m

    # top 10 by absolute sum
    top = sorted(by_l_account.items(), key=lambda kv: abs(kv[1]), reverse=True)[:10]
    return {
        "entries_count": len(entries),
        "total_money_sum": total_money_sum,
        "by_l_account": dict(top),
    }


def _filter_by_marker(entries: list[dict[str, Any]], marker: str) -> list[dict[str, Any]]:
    marker_lower = marker.lower()
    matched = []
    for e in entries:
        memo = (e.get("memo") or "").strip().lower()
        item = (e.get("item") or "").strip().lower()
        if memo.startswith(marker_lower) or item.startswith(marker_lower):
            matched.append(e)
    matched.sort(key=lambda e: e.get("entry_date") or "", reverse=True)
    return matched


async def _do_reconcile(
    client: WhooingClient,
    csv_path: str | None,
    pdf_path: str | None,
    section_id: str,
    issuer: str,
    start: str,
    end: str,
) -> dict[str, Any] | None:
    """csv 우선 (둘 다 주면 csv). 결과는 verbose 회피 위해 missing 만 자세히."""
    from whooing_mcp.tools.reconcile import reconcile_csv, reconcile_pdf

    if csv_path:
        if not os.path.isabs(csv_path):
            raise ToolError("USER_INPUT", f"csv_path 는 절대 경로: {csv_path!r}")
        result = await reconcile_csv(
            client,
            csv_path=csv_path,
            section_id=section_id,
            issuer=issuer,
            start_date=start,
            end_date=end,
        )
        input_type = "csv"
    elif pdf_path:
        if not os.path.isabs(pdf_path):
            raise ToolError("USER_INPUT", f"pdf_path 는 절대 경로: {pdf_path!r}")
        result = await reconcile_pdf(
            client,
            pdf_path=pdf_path,
            section_id=section_id,
            issuer=issuer,
            start_date=start,
            end_date=end,
        )
        input_type = "pdf"
    else:
        return None

    return {
        "input_type": input_type,
        "summary": result["summary"],
        "missing_in_whooing": result.get("missing_in_whooing", []),
        "extra_in_whooing_count": result["summary"]["extra_in_whooing_count"],
        "adapter_used": result.get("adapter_used"),
    }


def _build_next_actions(
    ai_count: int,
    dup_count: int,
    reconcile: dict[str, Any] | None,
) -> list[str]:
    actions: list[str] = []
    if reconcile:
        miss = reconcile["summary"].get("missing_in_whooing_count", 0)
        extra = reconcile["summary"].get("extra_in_whooing_count", 0)
        if miss > 0:
            actions.append(
                f"[정산] {miss}건이 명세서에 있는데 후잉에 없음 → "
                "사용자 확인 후 공식 MCP 의 add_entry 로 입력 "
                "(memo='[ai] monthly_close: ...' 권장)."
            )
        if extra > 0:
            actions.append(
                f"[정산] {extra}건이 후잉에 있는데 명세서에 없음 → "
                "환불/현금/타카드 가능성 — 자동 삭제 X, 사용자 검토."
            )
    if dup_count > 0:
        actions.append(
            f"[중복] {dup_count}쌍 후보 — 사용자 확인 후 공식 MCP delete_entry."
        )
    if ai_count > 0:
        actions.append(
            f"[감사] LLM 입력 {ai_count}건 — 사용자가 한 번 훑어볼 가치."
        )
    if not actions:
        actions.append("이번 달 특이사항 없음. 모두 깔끔.")
    return actions
