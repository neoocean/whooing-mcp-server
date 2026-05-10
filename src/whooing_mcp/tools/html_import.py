"""whooing_import_html_statement — 카드사 HTML 보안메일 import 도구.

Playwright 헤드리스로 client-side 복호화 → 거래 추출 → PDF import 와 동일한
dedup + auto-categorize + 공식 MCP entries-create + tracking 파이프라인.
"""

from __future__ import annotations

import os
from typing import Any

from whooing_mcp.client import WhooingClient
from whooing_core.html_adapters import detect as html_detect
from whooing_core.html_adapters import known_issuers as html_known_issuers
from whooing_core.html_adapters.base import HtmlDecryptError
from whooing_core.html_adapters.hanacard_secure_mail import (
    parse_html_async as parse_hanacard_async,
)
from whooing_core.html_adapters.hyundaicard_secure_mail import (
    parse_html_async as parse_hyundaicard_async,
)
from whooing_mcp.models import ToolError
from whooing_mcp.tools.pdf_import import (
    _build_account_map,
    _empty_envelope,
    _find_existing_match,
    _log_one,
    _row_to_dict,
    _track,
    _widen_range,
)


async def import_html_statement(
    client: WhooingClient,
    html_path: str,
    section_id: str,
    r_account_id: str,
    password_env_var: str = "auto",
    issuer: str = "auto",
    card_label: str | None = None,
    dedup_tolerance_days: int = 2,
    auto_categorize: bool = True,
    fallback_l_account_id: str = "x50",
    dry_run: bool = True,
    confirm_insert: bool = False,
) -> dict[str, Any]:
    # ---- 입력 검증 ----
    if not isinstance(html_path, str) or not html_path.strip():
        raise ToolError("USER_INPUT", "html_path 가 비어있습니다.")
    if not os.path.isabs(html_path):
        raise ToolError("USER_INPUT", f"html_path 는 절대 경로여야 합니다: {html_path!r}")
    if not os.path.exists(html_path):
        raise ToolError("USER_INPUT", f"파일이 없습니다: {html_path}")
    if issuer != "auto" and issuer not in html_known_issuers():
        raise ToolError(
            "USER_INPUT",
            f"지원하지 않는 HTML issuer: {issuer!r}. 지원: {html_known_issuers()} 또는 'auto'.",
        )
    if not isinstance(r_account_id, str) or not r_account_id.strip():
        raise ToolError("USER_INPUT", "r_account_id (카드 매핑) 필수.")
    if not dry_run and not confirm_insert:
        raise ToolError(
            "USER_INPUT",
            "dry_run=False (실 입력) 시 confirm_insert=True 필수.",
        )

    # ---- 1. password 환경변수 ----
    # 한국 카드사 (하나/현대/...) 모두 동일한 생년월일 6자리를 보안메일 패스워드로
    # 사용하므로 issuer 와 무관하게 단일 env 키 (WHOOING_CARD_HTML_PASSWORD) 공유.
    # backward-compat: 옛 키 (WHOOING_HANACARD_PASSWORD) 도 fallback 으로 인정.
    if password_env_var == "auto":
        password_env_var = _default_password_env_var(issuer)
    password = os.getenv(password_env_var, "").strip()
    if not password and password_env_var == "WHOOING_CARD_HTML_PASSWORD":
        legacy = os.getenv("WHOOING_HANACARD_PASSWORD", "").strip()
        if legacy:
            password = legacy
    if not password:
        raise ToolError(
            "USER_INPUT",
            f"환경변수 {password_env_var!r} 미설정 — .env 에 추가 필요.",
        )

    # ---- 2. issuer detect ----
    if issuer == "auto":
        d = html_detect(html_path)
        if d.detected_issuer is None:
            raise ToolError(
                "USER_INPUT",
                f"HTML format 자동 탐지 실패 — head excerpt: {d.head_excerpt[:200]!r}. "
                f"지원: {html_known_issuers()}. issuer 명시 또는 새 adapter 필요.",
            )
        issuer = d.detected_issuer

    try:
        if issuer == "hanacard_secure_mail":
            html_rows = await parse_hanacard_async(html_path, password)
        elif issuer == "hyundaicard_secure_mail":
            html_rows = await parse_hyundaicard_async(html_path, password)
        else:
            raise ToolError(
                "USER_INPUT",
                f"async 파서 미구현: {issuer!r} (현재 hanacard / hyundaicard secure_mail).",
            )
    except HtmlDecryptError as ex:
        raise ToolError("USER_INPUT", f"HTML 복호화/파싱 실패: {ex}")

    if not html_rows:
        return _empty_envelope_html(
            html_path, section_id, r_account_id, card_label, issuer, dry_run,
            note="HTML 에서 거래 행을 추출하지 못했습니다.",
        )

    # ---- 3. ledger fetch + dedup (PDF import 와 동일) ----
    html_dates = sorted({r.date for r in html_rows})
    fetch_start = html_dates[0]
    fetch_end = html_dates[-1]
    fetch_start, fetch_end = _widen_range(fetch_start, fetch_end, dedup_tolerance_days)

    ledger = await client.list_entries(
        section_id=section_id, start_date=fetch_start, end_date=fetch_end,
    )

    matched_existing: list[dict[str, Any]] = []
    new_proposals: list[dict[str, Any]] = []

    for row in html_rows:
        match = _find_existing_match(row, ledger, dedup_tolerance_days)
        if match:
            matched_existing.append({
                "html_row": _row_to_dict(row),
                "ledger_entry": {
                    "entry_id": match.get("entry_id"),
                    "entry_date": match.get("entry_date"),
                    "money": match.get("money"),
                    "item": match.get("item"),
                    "memo": match.get("memo"),
                },
            })
        else:
            new_proposals.append({"html_row": _row_to_dict(row), "_row_obj": row})

    # ---- 3. categorize ----
    accounts_dict = await client.list_accounts(section_id)
    account_map = _build_account_map(accounts_dict)

    from whooing_mcp.tools.category import suggest_category
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
                        name = top["l_account"]
                        for aid, (atype, aname) in account_map.items():
                            if aname == name:
                                l_account_id = aid
                                confidence = top["confidence"]
                                break
            except Exception:
                pass

        l_account_type, l_account_name = account_map.get(
            l_account_id, ("expenses", "(unknown)")
        )
        proposal["suggested_l_account_id"] = l_account_id
        proposal["suggested_l_account_name"] = l_account_name
        proposal["suggested_l_account_type"] = l_account_type
        proposal["category_confidence"] = round(confidence, 3)

        memo_parts = [f"HTML: {fetch_start[:6]}"]
        if card_label:
            memo_parts[-1] += f" {card_label}"
        if row_obj.amount < 0:
            memo_parts.append("환불/할인")
        proposal["memo"] = " — ".join(memo_parts)
        del proposal["_row_obj"]

    # ---- 4. dry_run / insert 분기 ----
    if dry_run:
        log_ids = _track_html(
            new_proposals, html_path, section_id, r_account_id, card_label,
            fetch_start, fetch_end, issuer, status="dry_run",
        )
        return {
            "html_path": html_path,
            "section_id": section_id,
            "r_account_id": r_account_id,
            "card_label": card_label,
            "issuer_used": issuer,
            "summary": {
                "html_total": len(html_rows),
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
                f"DRY_RUN — {len(new_proposals)}건 입력 대상, "
                f"{len(matched_existing)}건 이미 ledger 에 있음. "
                "실 입력하려면 dry_run=False, confirm_insert=True."
            ),
        }

    # 5. 실 insert (PDF import 와 동일 흐름 — 공식 MCP entries-create)
    return await _insert_via_official_mcp(
        new_proposals, html_path, section_id, r_account_id, card_label, issuer,
        fetch_start, fetch_end, account_map, matched_existing, html_rows,
    )


async def _insert_via_official_mcp(
    proposals, html_path, section_id, r_account_id, card_label, issuer,
    fetch_start, fetch_end, account_map, matched_existing, html_rows,
) -> dict[str, Any]:
    """proposals 를 공식 MCP entries-create 로 insert."""
    import asyncio
    import time
    from whooing_mcp.official_mcp import OfficialMcpClient, OfficialMcpError
    from whooing_mcp.tools.pdf_import import _extract_entry_id

    inserted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    log_ids: list[int] = []
    rate_window: list[float] = []
    _RPM_CAP = 18

    token = os.getenv("WHOOING_AI_TOKEN", "").strip()
    if not token:
        raise ToolError("AUTH", "WHOOING_AI_TOKEN 미설정.")
    mcp_client = OfficialMcpClient(token=token)

    for proposal in proposals:
        now_t = time.monotonic()
        rate_window = [t for t in rate_window if now_t - t < 60]
        if len(rate_window) >= _RPM_CAP:
            wait = 60 - (now_t - rate_window[0]) + 0.5
            await asyncio.sleep(wait)
            rate_window = []

        html_row = proposal["html_row"]
        l_account_id = proposal["suggested_l_account_id"]
        l_account_type = proposal["suggested_l_account_type"]
        r_account_type, _ = account_map.get(r_account_id, ("liabilities", ""))

        try:
            result = await mcp_client.call_tool("entries-create", {
                "section_id": section_id,
                "entry_date": html_row["date"],
                "l_account": l_account_type, "l_account_id": l_account_id,
                "r_account": r_account_type, "r_account_id": r_account_id,
                "money": html_row["amount"] + html_row["fee"],
                "item": html_row["merchant"],
                "memo": proposal["memo"],
            })
            rate_window.append(time.monotonic())
            new_entry_id = _extract_entry_id(result)
            inserted.append({"html_row": html_row, "entry_id": new_entry_id})
            log_id = _log_one(
                html_row, html_path, section_id, r_account_id, card_label,
                fetch_start, fetch_end, issuer,
                l_account_id=l_account_id,
                whooing_entry_id=str(new_entry_id) if new_entry_id else None,
                status="inserted", error_message=None,
                source_kind="html",
            )
            log_ids.append(log_id)
        except OfficialMcpError as ex:
            rate_window.append(time.monotonic())
            failed.append({"html_row": html_row, "error": str(ex)})
            log_id = _log_one(
                html_row, html_path, section_id, r_account_id, card_label,
                fetch_start, fetch_end, issuer,
                l_account_id=l_account_id,
                whooing_entry_id=None,
                status="failed", error_message=str(ex),
                source_kind="html",
            )
            log_ids.append(log_id)

    return {
        "html_path": html_path,
        "section_id": section_id,
        "r_account_id": r_account_id,
        "card_label": card_label,
        "issuer_used": issuer,
        "summary": {
            "html_total": len(html_rows),
            "matched_existing_count": len(matched_existing),
            "new_proposed_count": len(proposals),
            "new_inserted_count": len(inserted),
            "failed_count": len(failed),
        },
        "proposed": proposals,
        "matched_existing": matched_existing,
        "inserted": inserted,
        "failed": failed,
        "tracking_log_ids": log_ids,
        "dry_run": False,
        "note": (
            f"입력 완료: {len(inserted)} success / {len(failed)} fail. "
            f"{len(matched_existing)}건은 dedup 으로 skip. "
            "statement_import_log 동기화됨."
        ),
    }


def _empty_envelope_html(html_path, section_id, r_account_id, card_label, issuer,
                         dry_run, *, note: str) -> dict[str, Any]:
    return {
        "html_path": html_path, "section_id": section_id, "r_account_id": r_account_id,
        "card_label": card_label, "issuer_used": issuer,
        "summary": {"html_total": 0, "matched_existing_count": 0,
                    "new_proposed_count": 0, "new_inserted_count": 0, "failed_count": 0},
        "proposed": [], "matched_existing": [], "inserted": [], "failed": [],
        "tracking_log_ids": [], "dry_run": dry_run, "note": note,
    }


def _default_password_env_var(issuer: str) -> str:
    """카드사 보안메일 패스워드 env var default — 모든 issuer 가 공통.

    한국 카드사 (하나/현대/삼성/...) 는 모두 사용자 생년월일 6자리를 보안메일
    패스워드로 사용 → 단일 env 키 (`WHOOING_CARD_HTML_PASSWORD`) 공유.
    """
    return "WHOOING_CARD_HTML_PASSWORD"


def _track_html(proposals, html_path, section_id, r_account_id, card_label,
                period_start, period_end, issuer, status: str) -> list[int]:
    """HTML 전용 tracking — pdf_import._track 과 동일하나 source_kind='html'."""
    ids = []
    for p in proposals:
        log_id = _log_one(
            p["html_row"], html_path, section_id, r_account_id, card_label,
            period_start, period_end, issuer,
            l_account_id=p.get("suggested_l_account_id", ""),
            whooing_entry_id=None,
            status=status, error_message=None,
            source_kind="html",
        )
        ids.append(log_id)
    return ids
