"""whooing_reconcile_csv + whooing_csv_format_detect — DESIGN v2 §6.4, §6.5.

CSV 명세서와 후잉 entries 를 매칭해 누락/잉여를 보고. 입력 안 함.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any

from rapidfuzz import fuzz

from whooing_mcp.client import WhooingClient
from whooing_mcp.csv_adapters import detect, known_issuers, parse
from whooing_mcp.dates import date_diff_days, parse_yyyymmdd
from whooing_mcp.models import ToolError
from whooing_mcp.pdf_adapters import detect as pdf_detect
from whooing_mcp.pdf_adapters import known_issuers as pdf_known_issuers
from whooing_mcp.pdf_adapters import parse as pdf_parse


# ---- whooing_csv_format_detect ------------------------------------------


async def csv_format_detect(csv_path: str) -> dict[str, Any]:
    if not isinstance(csv_path, str) or not csv_path.strip():
        raise ToolError("USER_INPUT", "csv_path 가 비어있습니다.")
    if not os.path.isabs(csv_path):
        raise ToolError("USER_INPUT", f"csv_path 는 절대 경로여야 합니다: {csv_path!r}")
    if not os.path.exists(csv_path):
        raise ToolError("USER_INPUT", f"파일이 없습니다: {csv_path}")

    try:
        d = detect(csv_path)
    except Exception as ex:
        raise ToolError("USER_INPUT", f"CSV 읽기 실패: {ex}")

    return {
        "detected_issuer": d.detected_issuer,
        "confidence": round(d.confidence, 3),
        "header_sample": d.header_sample,
        "column_mapping_proposed": d.column_mapping_proposed,
        "supported_issuers": known_issuers(),
        "note": (
            "detected_issuer 가 None 이면 헤더 키워드가 매칭되지 않은 것입니다. "
            "header_sample 을 보고 어떤 카드사 포맷인지 확인 후, 새 adapter 를 "
            "추가하거나 기존 adapter 의 키워드를 보강하세요."
        ),
    }


# ---- whooing_reconcile_csv ----------------------------------------------


async def reconcile_csv(
    client: WhooingClient,
    csv_path: str,
    section_id: str,
    issuer: str = "auto",
    start_date: str | None = None,
    end_date: str | None = None,
    tolerance_days: int = 2,
    tolerance_amount: int = 0,
) -> dict[str, Any]:
    # ---- 입력 검증 ----
    if not os.path.isabs(csv_path):
        raise ToolError("USER_INPUT", f"csv_path 는 절대 경로여야 합니다: {csv_path!r}")
    if not os.path.exists(csv_path):
        raise ToolError("USER_INPUT", f"파일이 없습니다: {csv_path}")
    if issuer != "auto" and issuer not in known_issuers():
        raise ToolError(
            "USER_INPUT",
            f"지원하지 않는 issuer: {issuer!r}. 지원: {known_issuers()} 또는 'auto'.",
            supported=known_issuers(),
        )
    if tolerance_days < 0 or tolerance_days > 30:
        raise ToolError("USER_INPUT", f"tolerance_days 는 0~30 (받음: {tolerance_days})")
    if tolerance_amount < 0:
        raise ToolError("USER_INPUT", f"tolerance_amount 는 0 이상 (받음: {tolerance_amount})")

    # ---- CSV 파싱 ----
    try:
        adapter_used, csv_rows = parse(csv_path, issuer=issuer)
    except ValueError as ex:
        raise ToolError("USER_INPUT", str(ex), supported=known_issuers())

    # ---- 날짜 범위 결정 ----
    # 빈 CSV 라도 사용자가 start/end 명시했으면 후잉 entries 는 fetch 해서
    # 모두 extra_in_whooing 으로 보고한다 (정산 흐름 — '내 후잉에 안찍힌
    # 게 있나' 확인용으로 의미 있음).
    if csv_rows:
        csv_dates = [r.date for r in csv_rows]
        if start_date is None:
            start_date = min(csv_dates)
        if end_date is None:
            end_date = max(csv_dates)
    elif start_date is None or end_date is None:
        return _empty_envelope(adapter_used, section_id, csv_path,
                               start_date, end_date, tolerance_days, tolerance_amount)

    try:
        parse_yyyymmdd(start_date)
        parse_yyyymmdd(end_date)
    except ValueError as ex:
        raise ToolError("USER_INPUT", str(ex))
    if start_date > end_date:
        raise ToolError("USER_INPUT", f"start_date({start_date}) > end_date({end_date})")

    # tolerance_days 만큼 양쪽 확장 — 경계 근처 거래도 매칭 후보로 잡음
    fetch_start, fetch_end = _widen_range(start_date, end_date, tolerance_days)

    # ---- 후잉 entries fetch (tolerance 만큼 확장된 범위로) ----
    entries = await client.list_entries(
        section_id=section_id,
        start_date=fetch_start,
        end_date=fetch_end,
    )

    # ---- 매칭 ----
    matched, csv_remaining, whooing_remaining = _match(
        csv_rows, entries, tolerance_days, tolerance_amount
    )

    return {
        "summary": {
            "csv_total": len(csv_rows),
            "whooing_total": len(entries),
            "matched_count": len(matched),
            "missing_in_whooing_count": len(csv_remaining),
            "extra_in_whooing_count": len(whooing_remaining),
        },
        "matched": matched,
        "missing_in_whooing": [asdict(r) for r in csv_remaining],
        "extra_in_whooing": whooing_remaining,
        "adapter_used": adapter_used,
        "section_id": section_id,
        "date_range": {"start": start_date, "end": end_date},
        "params": {
            "tolerance_days": tolerance_days,
            "tolerance_amount": tolerance_amount,
        },
        "note": (
            "missing_in_whooing 항목은 LLM 이 사용자에게 보여주고 확인 후 "
            "공식 MCP 의 add_entry 로 입력하세요. extra_in_whooing 은 "
            "후잉에는 있는데 CSV 에 없는 거래 — 환불/현금/타카드 등 정상일 "
            "수 있으니 자동 삭제 X."
        ),
    }


# ---- whooing_pdf_format_detect ------------------------------------------


async def pdf_format_detect(pdf_path: str) -> dict[str, Any]:
    if not isinstance(pdf_path, str) or not pdf_path.strip():
        raise ToolError("USER_INPUT", "pdf_path 가 비어있습니다.")
    if not os.path.isabs(pdf_path):
        raise ToolError("USER_INPUT", f"pdf_path 는 절대 경로여야 합니다: {pdf_path!r}")
    if not os.path.exists(pdf_path):
        raise ToolError("USER_INPUT", f"파일이 없습니다: {pdf_path}")

    try:
        d = pdf_detect(pdf_path)
    except Exception as ex:
        raise ToolError("USER_INPUT", f"PDF 읽기 실패: {ex}")

    return {
        "detected_issuer": d.detected_issuer,
        "confidence": round(d.confidence, 3),
        "first_page_excerpt": d.first_page_excerpt,
        "supported_issuers": pdf_known_issuers(),
        "note": (
            "detected_issuer 가 None 이면 첫 페이지 텍스트에 알려진 카드사 "
            "키워드 미발견. first_page_excerpt 를 보고 새 adapter 추가 또는 "
            "기존 adapter 키워드 보강."
        ),
    }


# ---- whooing_reconcile_pdf ----------------------------------------------


async def reconcile_pdf(
    client: WhooingClient,
    pdf_path: str,
    section_id: str,
    issuer: str = "auto",
    start_date: str | None = None,
    end_date: str | None = None,
    tolerance_days: int = 2,
    tolerance_amount: int = 0,
) -> dict[str, Any]:
    """CSV 의 reconcile_csv 와 동일한 흐름. PDF → CSVRow → 같은 매칭 알고리즘."""
    if not os.path.isabs(pdf_path):
        raise ToolError("USER_INPUT", f"pdf_path 는 절대 경로여야 합니다: {pdf_path!r}")
    if not os.path.exists(pdf_path):
        raise ToolError("USER_INPUT", f"파일이 없습니다: {pdf_path}")
    if issuer != "auto" and issuer not in pdf_known_issuers():
        raise ToolError(
            "USER_INPUT",
            f"지원하지 않는 PDF issuer: {issuer!r}. 지원: {pdf_known_issuers()} 또는 'auto'.",
            supported=pdf_known_issuers(),
        )
    if tolerance_days < 0 or tolerance_days > 30:
        raise ToolError("USER_INPUT", f"tolerance_days 는 0~30 (받음: {tolerance_days})")
    if tolerance_amount < 0:
        raise ToolError("USER_INPUT", f"tolerance_amount 는 0 이상 (받음: {tolerance_amount})")

    try:
        adapter_used, pdf_rows = pdf_parse(pdf_path, issuer=issuer)
    except ValueError as ex:
        raise ToolError("USER_INPUT", str(ex), supported=pdf_known_issuers())

    if pdf_rows:
        pdf_dates = [r.date for r in pdf_rows]
        if start_date is None:
            start_date = min(pdf_dates)
        if end_date is None:
            end_date = max(pdf_dates)
    elif start_date is None or end_date is None:
        return _empty_envelope(adapter_used, section_id, pdf_path,
                               start_date, end_date, tolerance_days, tolerance_amount)

    try:
        parse_yyyymmdd(start_date)
        parse_yyyymmdd(end_date)
    except ValueError as ex:
        raise ToolError("USER_INPUT", str(ex))
    if start_date > end_date:
        raise ToolError("USER_INPUT", f"start_date({start_date}) > end_date({end_date})")

    fetch_start, fetch_end = _widen_range(start_date, end_date, tolerance_days)
    entries = await client.list_entries(
        section_id=section_id,
        start_date=fetch_start,
        end_date=fetch_end,
    )

    matched, pdf_remaining, whooing_remaining = _match(
        pdf_rows, entries, tolerance_days, tolerance_amount
    )

    return {
        "summary": {
            "csv_total": len(pdf_rows),  # CSV 호환 필드명 유지 (dataclass)
            "whooing_total": len(entries),
            "matched_count": len(matched),
            "missing_in_whooing_count": len(pdf_remaining),
            "extra_in_whooing_count": len(whooing_remaining),
        },
        "matched": matched,
        "missing_in_whooing": [asdict(r) for r in pdf_remaining],
        "extra_in_whooing": whooing_remaining,
        "adapter_used": adapter_used,
        "input_type": "pdf",
        "section_id": section_id,
        "date_range": {"start": start_date, "end": end_date},
        "params": {
            "tolerance_days": tolerance_days,
            "tolerance_amount": tolerance_amount,
        },
        "note": (
            "PDF 추출 결과 기반. missing_in_whooing 항목은 LLM 이 사용자 확인 후 "
            "공식 MCP add_entry 로 입력. extra_in_whooing 은 환불/현금/타카드/PDF 누락 "
            "등 정상일 수 있음 — 자동 삭제 X."
        ),
    }


def _widen_range(start: str, end: str, tolerance_days: int) -> tuple[str, str]:
    """매칭 tolerance 를 위해 ±tolerance_days 확장."""
    from datetime import datetime, timedelta
    if tolerance_days <= 0:
        return start, end
    s = datetime.strptime(start, "%Y%m%d") - timedelta(days=tolerance_days)
    e = datetime.strptime(end, "%Y%m%d") + timedelta(days=tolerance_days)
    return s.strftime("%Y%m%d"), e.strftime("%Y%m%d")


def _empty_envelope(adapter_used, section_id, csv_path, start, end, td, ta):
    return {
        "summary": {
            "csv_total": 0,
            "whooing_total": 0,
            "matched_count": 0,
            "missing_in_whooing_count": 0,
            "extra_in_whooing_count": 0,
        },
        "matched": [],
        "missing_in_whooing": [],
        "extra_in_whooing": [],
        "adapter_used": adapter_used,
        "section_id": section_id,
        "date_range": {"start": start, "end": end},
        "params": {"tolerance_days": td, "tolerance_amount": ta},
        "note": "CSV 가 비어있습니다.",
    }


def _match(
    csv_rows,
    whooing_entries,
    tolerance_days: int,
    tolerance_amount: int,
):
    """greedy 1-1 매칭 — 각 csv_row 에 대해 최고 score 의 whooing 후보."""
    csv_remaining = list(csv_rows)
    whooing_remaining = list(whooing_entries)
    matched: list[dict[str, Any]] = []

    # 결정성을 위해 csv 를 date asc 정렬
    csv_remaining.sort(key=lambda r: r.date)

    for csv_row in list(csv_remaining):
        best: tuple[float, dict] | None = None
        for w in whooing_remaining:
            wd = w.get("entry_date")
            wm = w.get("money")
            if not wd or wm is None:
                continue
            try:
                date_diff = date_diff_days(csv_row.date, wd)
            except ValueError:
                continue
            if date_diff > tolerance_days:
                continue
            try:
                wm_int = int(wm)
            except (TypeError, ValueError):
                continue
            money_diff = abs(csv_row.amount - wm_int)
            if money_diff > tolerance_amount:
                continue

            # score 계산
            date_pen = (date_diff / max(1, tolerance_days)) * 0.3 if tolerance_days > 0 else 0
            money_pen = (money_diff / max(1, tolerance_amount)) * 0.3 if tolerance_amount > 0 else 0
            sim = float(fuzz.ratio(csv_row.merchant, w.get("item") or "")) / 100.0
            score = 1.0 - date_pen - money_pen + sim * 0.4

            if best is None or score > best[0]:
                best = (score, w)

        if best is not None:
            matched.append({
                "csv_row": asdict(csv_row),
                "whooing_entry": best[1],
                "score": round(best[0], 3),
            })
            whooing_remaining.remove(best[1])
            csv_remaining.remove(csv_row)

    return matched, csv_remaining, whooing_remaining
