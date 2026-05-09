"""whooing_import_pdf_statement — 정식 PDF 카드명세서 import 도구.

CL 50708 의 일회용 스크립트 (tests/_pdf_import_2026_04.py) 를 일반화. dedup
+ auto-categorize + 공식 MCP 통한 안전한 insert + statement_import_log
tracking + dry_run safety default.

흐름:
  1. PDF 파싱 (pdf_adapters/) → CSVRow 리스트
  2. ledger fetch (paginated client.list_entries) — 같은 카드 + 일치 amount/date
     매칭 시 'matched_existing' 으로 분류 (insert 안 함)
  3. 누락 (new) 항목 마다:
     a. auto_categorize=True 면 suggest_category 로 l_account_id 추정
     b. confidence 부족하면 fallback_l_account_id (default x50 식비)
     c. dry_run 이면 'proposed' 로만 보고
     d. insert 모드면 official MCP entries-create + statement_import_log 기록
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from typing import Any

from rapidfuzz import fuzz

from whooing_mcp.client import WhooingClient
from whooing_mcp.dates import KST, date_diff_days, parse_yyyymmdd
from whooing_mcp.models import ToolError
from whooing_mcp.official_mcp import OfficialMcpClient, OfficialMcpError
from whooing_mcp.pdf_adapters import detect as pdf_detect
from whooing_mcp.pdf_adapters import known_issuers as pdf_known_issuers
from whooing_mcp.pdf_adapters import parse as pdf_parse
from whooing_mcp.queue import open_db
from whooing_mcp.tools.category import suggest_category


_RPM_CAP = 18  # official MCP rate limit (서버 한도 20 — buffer)


def _now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


async def import_pdf_statement(
    client: WhooingClient,
    pdf_path: str,
    section_id: str,
    r_account_id: str,
    issuer: str = "auto",
    card_label: str | None = None,
    dedup_tolerance_days: int = 2,
    auto_categorize: bool = True,
    fallback_l_account_id: str = "x50",
    dry_run: bool = True,
    confirm_insert: bool = False,
) -> dict[str, Any]:
    # ---- 입력 검증 ----
    if not isinstance(pdf_path, str) or not pdf_path.strip():
        raise ToolError("USER_INPUT", "pdf_path 가 비어있습니다.")
    if not os.path.isabs(pdf_path):
        raise ToolError("USER_INPUT", f"pdf_path 는 절대 경로여야 합니다: {pdf_path!r}")
    if not os.path.exists(pdf_path):
        raise ToolError("USER_INPUT", f"파일이 없습니다: {pdf_path}")
    if issuer != "auto" and issuer not in pdf_known_issuers():
        raise ToolError(
            "USER_INPUT",
            f"지원하지 않는 PDF issuer: {issuer!r}. 지원: {pdf_known_issuers()} 또는 'auto'.",
        )
    if not isinstance(r_account_id, str) or not r_account_id.strip():
        raise ToolError(
            "USER_INPUT",
            "r_account_id (카드 매핑) 필수. 예: 'x80' (하나카드). "
            "후잉 가계부의 어느 부채/자산 계정으로 입력할지 명시.",
        )
    if not dry_run and not confirm_insert:
        raise ToolError(
            "USER_INPUT",
            "dry_run=False (실 입력) 시 confirm_insert=True 필수. "
            "재무 데이터 추가 — 의도 확인 가드.",
        )

    # ---- 1. PDF 파싱 ----
    try:
        adapter_used, pdf_rows = pdf_parse(pdf_path, issuer=issuer)
    except ValueError as ex:
        raise ToolError("USER_INPUT", str(ex), supported=pdf_known_issuers())

    if not pdf_rows:
        return _empty_envelope(
            pdf_path, section_id, r_account_id, card_label, adapter_used, dry_run,
            note="PDF 에서 행을 추출하지 못했습니다 (이미지 PDF 또는 미지원 카드사 형식). "
                 "이미지 PDF 라면 vision-based workflow (SMS parser 또는 직접 LLM 입력) 권장.",
        )

    # ---- 2. ledger fetch + dedup ----
    pdf_dates = sorted({r.date for r in pdf_rows})
    fetch_start = pdf_dates[0]
    fetch_end = pdf_dates[-1]
    # tolerance window 양쪽 확장
    fetch_start, fetch_end = _widen_range(fetch_start, fetch_end, dedup_tolerance_days)

    ledger = await client.list_entries(
        section_id=section_id,
        start_date=fetch_start,
        end_date=fetch_end,
    )

    matched_existing: list[dict[str, Any]] = []
    new_proposals: list[dict[str, Any]] = []

    for row in pdf_rows:
        match = _find_existing_match(row, ledger, dedup_tolerance_days)
        if match:
            matched_existing.append({
                "pdf_row": _row_to_dict(row),
                "ledger_entry": {
                    "entry_id": match.get("entry_id"),
                    "entry_date": match.get("entry_date"),
                    "money": match.get("money"),
                    "item": match.get("item"),
                    "memo": match.get("memo"),
                    "l_account_id": match.get("l_account_id"),
                    "r_account_id": match.get("r_account_id"),
                },
            })
        else:
            new_proposals.append({"pdf_row": _row_to_dict(row), "_row_obj": row})

    # ---- 3. categorize (auto) ----
    accounts_dict = await client.list_accounts(section_id)
    account_map = _build_account_map(accounts_dict)

    for proposal in new_proposals:
        row_obj = proposal["_row_obj"]
        l_account_id = fallback_l_account_id
        confidence = 0.0
        if auto_categorize:
            try:
                sugg = await suggest_category(
                    client,
                    merchant=row_obj.merchant,
                    section_id=section_id,
                    lookback_days=180,
                )
                if sugg.get("suggested"):
                    top = sugg["suggested"][0]
                    if top["confidence"] >= 0.5:
                        # 'l_account' in suggest_category 결과는 사람 친화 이름 (식비)
                        # 우리는 ID 가 필요 — account_map 에서 name → id 역매핑
                        name = top["l_account"]
                        # account_map: id → (type, name); 역매핑 inline
                        for aid, (atype, aname) in account_map.items():
                            if aname == name:
                                l_account_id = aid
                                confidence = top["confidence"]
                                break
            except Exception as ex:
                # suggest_category 실패는 fatal X — fallback 사용
                pass

        # account_id → type 매핑
        l_account_type, l_account_name = account_map.get(
            l_account_id, ("expenses", "(unknown)")
        )

        proposal["suggested_l_account_id"] = l_account_id
        proposal["suggested_l_account_name"] = l_account_name
        proposal["suggested_l_account_type"] = l_account_type
        proposal["category_confidence"] = round(confidence, 3)

        # memo 자동 생성 (tracking 용)
        memo_parts = []
        if card_label:
            memo_parts.append(f"PDF: {fetch_start[:6]} {card_label}")
        else:
            memo_parts.append(f"PDF: {fetch_start[:6]}")
        if row_obj.amount < 0:
            memo_parts.append("환불/할인")
        proposal["memo"] = " — ".join(memo_parts)

        # _row_obj 는 client에서만 사용 — 응답에서 제거
        del proposal["_row_obj"]

    # ---- 4. dry_run 이면 여기서 끝, 추적 log 만 'dry_run' 으로 기록 ----
    if dry_run:
        log_ids = _track(
            new_proposals, pdf_path, section_id, r_account_id, card_label,
            fetch_start, fetch_end, adapter_used, status="dry_run",
        )
        return {
            "pdf_path": pdf_path,
            "section_id": section_id,
            "r_account_id": r_account_id,
            "card_label": card_label,
            "adapter_used": adapter_used,
            "summary": {
                "pdf_total": len(pdf_rows),
                "matched_existing_count": len(matched_existing),
                "new_proposed_count": len(new_proposals),
                "new_inserted_count": 0,
                "failed_count": 0,
            },
            "proposed": new_proposals,
            "matched_existing": matched_existing,
            "inserted": [],
            "failed": [],
            "tracking_log_ids": log_ids,
            "dry_run": True,
            "note": (
                f"DRY_RUN 모드 — {len(new_proposals)}건이 입력 대상으로 식별. "
                "실 입력하려면 dry_run=False, confirm_insert=True 로 재호출. "
                f"{len(matched_existing)}건은 이미 ledger 에 있음 (skip)."
            ),
        }

    # ---- 5. 실 입력 (official MCP entries-create) ----
    inserted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    log_ids: list[int] = []
    rate_window: list[float] = []

    token = os.getenv("WHOOING_AI_TOKEN", "").strip()
    if not token:
        raise ToolError("AUTH", "WHOOING_AI_TOKEN 미설정.")
    mcp_client = OfficialMcpClient(token=token)

    for proposal in new_proposals:
        # rate limit
        now_t = time.monotonic()
        rate_window = [t for t in rate_window if now_t - t < 60]
        if len(rate_window) >= _RPM_CAP:
            wait = 60 - (now_t - rate_window[0]) + 0.5
            await asyncio.sleep(wait)
            rate_window = []

        pdf_row = proposal["pdf_row"]
        l_account_id = proposal["suggested_l_account_id"]
        l_account_type = proposal["suggested_l_account_type"]
        # r_account_type 은 인자 r_account_id 의 type — 보통 'liabilities' (카드)
        r_account_type, _ = account_map.get(r_account_id, ("liabilities", ""))

        try:
            result = await mcp_client.call_tool(
                "entries-create",
                {
                    "section_id": section_id,
                    "entry_date": pdf_row["date"],
                    "l_account": l_account_type,
                    "l_account_id": l_account_id,
                    "r_account": r_account_type,
                    "r_account_id": r_account_id,
                    "money": pdf_row["amount"] + pdf_row["fee"],  # total
                    "item": pdf_row["merchant"],
                    "memo": proposal["memo"],
                },
            )
            rate_window.append(time.monotonic())
            # entries-create result.content 또는 structuredContent 에 entry_id
            new_entry_id = _extract_entry_id(result)
            inserted.append({
                "pdf_row": pdf_row,
                "entry_id": new_entry_id,
                "l_account_id": l_account_id,
                "r_account_id": r_account_id,
            })
            log_id = _log_one(
                pdf_row, pdf_path, section_id, r_account_id, card_label,
                fetch_start, fetch_end, adapter_used,
                l_account_id=l_account_id,
                whooing_entry_id=str(new_entry_id) if new_entry_id else None,
                status="inserted", error_message=None,
            )
            log_ids.append(log_id)
        except OfficialMcpError as ex:
            rate_window.append(time.monotonic())
            failed.append({"pdf_row": pdf_row, "error": str(ex)})
            log_id = _log_one(
                pdf_row, pdf_path, section_id, r_account_id, card_label,
                fetch_start, fetch_end, adapter_used,
                l_account_id=l_account_id,
                whooing_entry_id=None,
                status="failed", error_message=str(ex),
            )
            log_ids.append(log_id)

    return {
        "pdf_path": pdf_path,
        "section_id": section_id,
        "r_account_id": r_account_id,
        "card_label": card_label,
        "adapter_used": adapter_used,
        "summary": {
            "pdf_total": len(pdf_rows),
            "matched_existing_count": len(matched_existing),
            "new_proposed_count": len(new_proposals),
            "new_inserted_count": len(inserted),
            "failed_count": len(failed),
        },
        "proposed": new_proposals,
        "matched_existing": matched_existing,
        "inserted": inserted,
        "failed": failed,
        "tracking_log_ids": log_ids,
        "dry_run": False,
        "note": (
            f"입력 완료: {len(inserted)} success / {len(failed)} fail. "
            f"{len(matched_existing)}건은 dedup 으로 skip. "
            "statement_import_log 동기화됨 (P4 sync 는 다음 modifying 호출 시 자동)."
        ),
    }


# ---- helpers ----------------------------------------------------------


def _row_to_dict(row) -> dict[str, Any]:
    """CSVRow 를 dict 로 (응답 직렬화 가능 형태)."""
    return {
        "date": row.date,
        "amount": row.amount,
        "fee": getattr(row, "fee", 0) if hasattr(row, "fee") else 0,
        "merchant": row.merchant,
        # raw 는 응답에 안 넣음 (verbose)
    }


def _widen_range(start: str, end: str, tolerance_days: int) -> tuple[str, str]:
    from datetime import datetime, timedelta
    if tolerance_days <= 0:
        return start, end
    s = datetime.strptime(start, "%Y%m%d") - timedelta(days=tolerance_days)
    e = datetime.strptime(end, "%Y%m%d") + timedelta(days=tolerance_days)
    return s.strftime("%Y%m%d"), e.strftime("%Y%m%d")


def _find_existing_match(
    row,
    ledger: list[dict[str, Any]],
    tolerance_days: int,
) -> dict[str, Any] | None:
    """row 와 매칭되는 기존 ledger entry — 가장 유사 item 후보."""
    candidates = []
    for L in ledger:
        ldate_raw = L.get("entry_date")
        if not ldate_raw:
            continue
        ldate = str(ldate_raw).split(".")[0]  # YYYYMMDD.NNNN → YYYYMMDD
        try:
            days = date_diff_days(row.date, ldate)
        except ValueError:
            continue
        if days > tolerance_days:
            continue
        lmoney = L.get("money")
        if lmoney is None:
            continue
        try:
            lmoney_int = int(lmoney)
        except (TypeError, ValueError):
            continue
        # match against amount, total, |amount|
        targets = {row.amount, row.amount + getattr(row, 'fee', 0)}
        targets.update({abs(t) for t in list(targets)})
        if lmoney_int not in targets:
            continue
        sim = fuzz.token_set_ratio(row.merchant, L.get("item") or "") / 100.0
        candidates.append((sim, L))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _build_account_map(accounts_dict: dict) -> dict[str, tuple[str, str]]:
    """{id: (type, name)} 매핑."""
    out: dict[str, tuple[str, str]] = {}
    for type_key, items in accounts_dict.items():
        if not isinstance(items, list):
            continue
        for a in items:
            aid = a.get("account_id") or a.get("id")
            if aid:
                out[str(aid)] = (type_key, a.get("title") or a.get("name") or "")
    return out


def _extract_entry_id(result: dict) -> str | None:
    """entries-create result 에서 새 entry_id 추출.

    공식 MCP 응답 형태 (live 검증):
      result = {content: [{type:'text', text:'...'}], structuredContent?: {...}}
    REST 직접 호출 시 (CL 50708 검증): results 가 list 로 [{entry_id, ...}]
    공식 MCP 의 content 에 어떤 형태로 들어오는지 case-by-case.
    """
    # structuredContent 우선
    sc = result.get("structuredContent")
    if isinstance(sc, dict):
        if "entry_id" in sc:
            return str(sc["entry_id"])
        if "result" in sc and isinstance(sc["result"], dict) and "entry_id" in sc["result"]:
            return str(sc["result"]["entry_id"])
    # content[].text 에서 entry_id 패턴 찾기
    content = result.get("content") or []
    for c in content:
        if isinstance(c, dict) and c.get("type") == "text":
            text = c.get("text", "")
            # JSON 텍스트일 가능성
            try:
                import json
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    if "entry_id" in parsed:
                        return str(parsed["entry_id"])
                    if "results" in parsed and isinstance(parsed["results"], list):
                        if parsed["results"] and "entry_id" in parsed["results"][0]:
                            return str(parsed["results"][0]["entry_id"])
            except json.JSONDecodeError:
                pass
    return None


def _track(
    proposals: list[dict],
    pdf_path: str,
    section_id: str,
    r_account_id: str,
    card_label: str | None,
    period_start: str,
    period_end: str,
    adapter_used: str,
    status: str,
) -> list[int]:
    """proposals 를 statement_import_log 에 batch insert (dry_run 또는 'pending')."""
    ids = []
    for p in proposals:
        log_id = _log_one(
            p["pdf_row"], pdf_path, section_id, r_account_id, card_label,
            period_start, period_end, adapter_used,
            l_account_id=p.get("suggested_l_account_id", ""),
            whooing_entry_id=None,
            status=status, error_message=None,
        )
        ids.append(log_id)
    return ids


def _log_one(
    pdf_row: dict,
    pdf_path: str,
    section_id: str,
    r_account_id: str,
    card_label: str | None,
    period_start: str,
    period_end: str,
    adapter_used: str,
    *,
    l_account_id: str,
    whooing_entry_id: str | None,
    status: str,
    error_message: str | None,
    source_kind: str = "pdf",
) -> int:
    """statement_import_log 1행 insert. source_kind 는 'pdf'|'csv'|'html'."""
    fee = pdf_row.get("fee", 0)
    is_foreign = fee > 0
    with open_db() as conn:
        cur = conn.execute(
            """INSERT INTO statement_import_log
               (source_file, source_kind, statement_period_start, statement_period_end,
                issuer, card_label, entry_date, merchant, original_amount, fee_amount,
                total_amount, currency, foreign_amount, exchange_rate,
                section_id, l_account_id, r_account_id,
                whooing_entry_id, status, imported_at, error_message, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pdf_path, source_kind, period_start, period_end,
             adapter_used, card_label,
             pdf_row["date"], pdf_row["merchant"],
             pdf_row["amount"], fee, pdf_row["amount"] + fee,
             "USD" if is_foreign else "KRW",
             None, None,
             section_id, l_account_id, r_account_id,
             whooing_entry_id, status, _now_iso(), error_message, None),
        )
        return cur.lastrowid


def _empty_envelope(pdf_path, section_id, r_account_id, card_label, adapter_used,
                    dry_run, *, note: str) -> dict:
    return {
        "pdf_path": pdf_path,
        "section_id": section_id,
        "r_account_id": r_account_id,
        "card_label": card_label,
        "adapter_used": adapter_used,
        "summary": {"pdf_total": 0, "matched_existing_count": 0,
                    "new_proposed_count": 0, "new_inserted_count": 0, "failed_count": 0},
        "proposed": [], "matched_existing": [], "inserted": [], "failed": [],
        "tracking_log_ids": [], "dry_run": dry_run, "note": note,
    }
