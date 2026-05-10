"""MCP server entrypoint — FastMCP + 도구 등록 + bootstrap.

- stdio 트랜스포트 (Claude Desktop / Claude Code 표준).
- 자격증명: WHOOING_AI_TOKEN 환경변수 또는 .env (cwd → ~/.config/whooing-mcp/.env).
- section_id resolve 우선순위:
    explicit override (도구 인자) > WHOOING_SECTION_ID env > 첫 섹션 자동.
- 모든 로깅은 stderr 로 (stdio 트랜스포트는 stdout 을 MCP framing 에 씀).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from whooing_mcp.auth import WhooingAuth
from whooing_mcp.client import WhooingClient
from whooing_mcp.models import ToolError
from whooing_mcp.tools.audit import DEFAULT_MARKER, audit_recent_ai_entries
from whooing_mcp.tools.category import suggest_category
from whooing_mcp.tools.delete import delete_entries
from whooing_mcp.tools.dedup import find_duplicates
from whooing_mcp.tools.monthly_close import monthly_close
from whooing_mcp.tools.pending import (
    confirm_pending,
    dismiss_pending,
    enqueue_pending,
    list_pending,
)
from whooing_mcp.tools.reconcile import (
    csv_format_detect,
    pdf_format_detect,
    reconcile_csv,
    reconcile_pdf,
)
from whooing_mcp.tools.sms import parse_payment_sms

log = logging.getLogger("whooing_mcp")


# ---- 환경/자격증명 부트스트랩 -----------------------------------------------


def _load_env() -> None:
    """`.env` 탐색 우선순위 (먼저 발견된 1개만 로드 — DESIGN §8.2):

      1. $WHOOING_MCP_ENV           (명시 override 경로)
      2. Path.cwd() / ".env"        (전통적 위치)
      3. <project root> / ".env"    (cwd 무관 — editable install 시 동작)
      4. ~/.config/whooing-mcp/.env (사용자 전역)

    Claude Desktop / Claude Code 는 cwd 가 프로젝트가 아닐 때가 많아 (3)
    이 결정적이다. `__file__` 이 `<project>/src/whooing_mcp/server.py` 이므로
    `parents[2]` 가 프로젝트 루트.
    """
    candidates: list[Path] = []

    explicit = os.getenv("WHOOING_MCP_ENV")
    if explicit:
        candidates.append(Path(explicit).expanduser())

    candidates.append(Path.cwd() / ".env")

    try:
        project_root = Path(__file__).resolve().parents[2]
        candidates.append(project_root / ".env")
    except IndexError:
        pass  # __file__ 위치가 예상과 다른 install (wheel 등)

    candidates.append(Path.home() / ".config" / "whooing-mcp" / ".env")

    for c in candidates:
        if c.exists():
            load_dotenv(c)
            log.info("loaded .env from %s", c)
            return

    log.warning(
        ".env not found. Tried: %s. WHOOING_AI_TOKEN must be set in process env.",
        [str(c) for c in candidates],
    )


def _build_client() -> tuple[WhooingClient, str | None]:
    token = os.getenv("WHOOING_AI_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "WHOOING_AI_TOKEN 미설정. "
            "후잉 → 사용자 > 계정 > 비밀번호 및 보안 > AI 토큰 발급 후 "
            ".env 또는 환경변수에 WHOOING_AI_TOKEN=__eyJh... 설정하세요."
        )

    # Sanity check (DESIGN §13). 잘못된 토큰을 미리 잡아 401 시간 절약.
    # 형식이 바뀔 가능성이 있어 fail 은 X — 경고만.
    if not token.startswith("__"):
        log.warning(
            "WHOOING_AI_TOKEN prefix 가 '__' 가 아님 (%r). 후잉 AI 토큰은 "
            "보통 '__eyJh...' 로 시작합니다. .env 의 토큰 값 재확인 권장.",
            token[:4],
        )
    if len(token) < 50:
        log.warning(
            "WHOOING_AI_TOKEN 길이 (%d) 가 비정상적으로 짧음. 후잉 토큰은 "
            "보통 100자 이상. 잘림/오타 가능성 — .env 재확인 권장.",
            len(token),
        )

    base = os.getenv("WHOOING_BASE_URL", "https://whooing.com/api")
    timeout = float(os.getenv("WHOOING_HTTP_TIMEOUT", "10"))
    rpm_cap = int(os.getenv("WHOOING_RPM_CAP", "20"))
    auth = WhooingAuth(token=token)
    log.info(
        "client built (auth=%r base=%s timeout=%.1f rpm_cap=%d)",
        auth, base, timeout, rpm_cap,
    )
    section_id = (os.getenv("WHOOING_SECTION_ID") or "").strip() or None
    return WhooingClient(auth=auth, base_url=base, timeout=timeout, rpm_cap=rpm_cap), section_id


# ---- 모듈 전역 (FastMCP 데코레이터에서 접근) --------------------------------

_CLIENT: WhooingClient | None = None
_DEFAULT_SECTION_ID: str | None = None


async def _ensure_client_and_section(override: str | None) -> tuple[WhooingClient, str]:
    """첫 호출 시 lazy bootstrap. section_id 우선순위는 함수 docstring 참조."""
    global _CLIENT, _DEFAULT_SECTION_ID
    if _CLIENT is None:
        _CLIENT, env_sid = _build_client()
        if env_sid:
            _DEFAULT_SECTION_ID = env_sid

    if override:
        return _CLIENT, override
    if _DEFAULT_SECTION_ID:
        return _CLIENT, _DEFAULT_SECTION_ID

    sections = await _CLIENT.list_sections()
    if not sections:
        raise ToolError(
            "USER_INPUT",
            "사용자가 가진 섹션이 없습니다. 후잉에서 가계부를 먼저 만드세요.",
        )
    first = sections[0]
    sid = first.get("section_id") or first.get("id")
    if not sid:
        raise ToolError("UPSTREAM", f"섹션 응답에 section_id 가 없습니다: {first!r}")
    _DEFAULT_SECTION_ID = str(sid)
    log.info("auto-selected first section: %s", _DEFAULT_SECTION_ID)
    return _CLIENT, _DEFAULT_SECTION_ID


# ---- MCP 서버 + 도구 등록 ----------------------------------------------------


def build_mcp() -> FastMCP:
    # MCP server self-name. Claude Desktop config 의 mcpServers 키 와는 별개 —
    # 사용자는 config 키를 짧게 (예: 'whooing-extras') 유지하는 게 편하지만
    # 서버 자체의 정체성은 외부 노출명을 따른다.
    mcp = FastMCP("whooing-mcp-server-wrapper")

    @mcp.tool(
        description=(
            "후잉 가계부에서 LLM 이 입력한 거래만 골라봅니다 (memo 접두 마커 기준). "
            "기본 마커는 '[ai]'. 사용자가 LLM 에 거래 입력을 위임할 때, LLM 은 "
            "공식 MCP 의 add_entry 호출 시 memo 첫 단어로 '[ai]' 를 붙여야 본 "
            "도구로 추적됩니다."
        )
    )
    async def whooing_audit_recent_ai_entries(
        days: int = 7,
        marker: str = DEFAULT_MARKER,
        section_id: str | None = None,
    ) -> dict:
        try:
            client, sid = await _ensure_client_and_section(section_id)
            return await audit_recent_ai_entries(
                client, section_id=sid, days=days, marker=marker
            )
        except ToolError as e:
            return {"error": {"kind": e.kind, "message": e.message, **e.details}}

    @mcp.tool(
        description=(
            "SMS / Push 결제 알림 텍스트 한 덩어리를 후잉 항목 후보(dict)로 "
            "변환합니다. 후잉 API 호출 없음. 결과를 사용자에게 보여주고 확인 "
            "받은 후, 공식 MCP 의 add_entry 로 입력하세요 (memo 첫 단어로 "
            "'[ai]' 권장)."
        )
    )
    async def whooing_parse_payment_sms(
        text: str,
        issuer_hint: str = "auto",
    ) -> dict:
        try:
            return await parse_payment_sms(text, issuer_hint=issuer_hint)
        except ToolError as e:
            return {"error": {"kind": e.kind, "message": e.message, **e.details}}

    @mcp.tool(
        description=(
            "후잉 가계부에서 같은 금액 + 유사 item + ±tolerance_days 안 거래쌍을 "
            "중복 후보로 반환합니다. 읽기 전용 — 결과는 _후보_ 만 보고하며, 실제 "
            "삭제는 사용자 확인 후 공식 MCP 의 delete_entry 로 처리하세요."
        )
    )
    async def whooing_find_duplicates(
        start_date: str,
        end_date: str,
        section_id: str | None = None,
        tolerance_days: int = 1,
        min_similarity: float = 0.85,
    ) -> dict:
        try:
            client, sid = await _ensure_client_and_section(section_id)
            return await find_duplicates(
                client,
                section_id=sid,
                start_date=start_date,
                end_date=end_date,
                tolerance_days=tolerance_days,
                min_similarity=min_similarity,
            )
        except ToolError as e:
            return {"error": {"kind": e.kind, "message": e.message, **e.details}}

    @mcp.tool(
        description=(
            "카드사 명세서 CSV 파일을 후잉 가계부 entries 와 매칭해 누락/잉여를 "
            "반환합니다. issuer 는 'auto' (header 로 탐지), 'shinhan_card', "
            "'kookmin_card' 중 하나. 읽기 전용 — 누락 항목 자동 입력 X, "
            "사용자 확인 후 공식 MCP 의 add_entry 로 처리하세요."
        )
    )
    async def whooing_reconcile_csv(
        csv_path: str,
        issuer: str = "auto",
        start_date: str | None = None,
        end_date: str | None = None,
        section_id: str | None = None,
        tolerance_days: int = 2,
        tolerance_amount: int = 0,
    ) -> dict:
        try:
            client, sid = await _ensure_client_and_section(section_id)
            return await reconcile_csv(
                client,
                csv_path=csv_path,
                section_id=sid,
                issuer=issuer,
                start_date=start_date,
                end_date=end_date,
                tolerance_days=tolerance_days,
                tolerance_amount=tolerance_amount,
            )
        except ToolError as e:
            return {"error": {"kind": e.kind, "message": e.message, **e.details}}

    @mcp.tool(
        description=(
            "CSV 파일 헤더만 보고 어떤 카드사 명세서 포맷인지 탐지합니다. "
            "whooing_reconcile_csv 가 issuer=auto 로 매칭 실패 시 디버깅용. "
            "API 호출 없음."
        )
    )
    async def whooing_csv_format_detect(csv_path: str) -> dict:
        try:
            return await csv_format_detect(csv_path)
        except ToolError as e:
            return {"error": {"kind": e.kind, "message": e.message, **e.details}}

    @mcp.tool(
        description=(
            "카드사 명세서 PDF 파일을 후잉 가계부 entries 와 매칭해 누락/잉여를 "
            "반환합니다. issuer 는 'auto' (PDF 첫 페이지 텍스트로 탐지), "
            "'shinhan_card', 'hyundai_card' 중 하나. 텍스트 추출 가능 PDF 만 "
            "지원 (이미지/스캔 PDF 는 OCR 필요 — 미지원). 읽기 전용 — 누락 항목 "
            "자동 입력 X."
        )
    )
    async def whooing_reconcile_pdf(
        pdf_path: str,
        issuer: str = "auto",
        start_date: str | None = None,
        end_date: str | None = None,
        section_id: str | None = None,
        tolerance_days: int = 2,
        tolerance_amount: int = 0,
    ) -> dict:
        try:
            client, sid = await _ensure_client_and_section(section_id)
            return await reconcile_pdf(
                client,
                pdf_path=pdf_path,
                section_id=sid,
                issuer=issuer,
                start_date=start_date,
                end_date=end_date,
                tolerance_days=tolerance_days,
                tolerance_amount=tolerance_amount,
            )
        except ToolError as e:
            return {"error": {"kind": e.kind, "message": e.message, **e.details}}

    @mcp.tool(
        description=(
            "PDF 파일 첫 페이지 텍스트로 카드사 추정 (디버깅용). "
            "whooing_reconcile_pdf 가 issuer=auto 로 매칭 실패 시 first_page_excerpt "
            "보고 새 adapter 추가/보강 판단."
        )
    )
    async def whooing_pdf_format_detect(pdf_path: str) -> dict:
        try:
            return await pdf_format_detect(pdf_path)
        except ToolError as e:
            return {"error": {"kind": e.kind, "message": e.message, **e.details}}

    @mcp.tool(
        description=(
            "사용자의 과거 후잉 거래에서 학습해 새 가맹점의 카테고리(l_account)를 "
            "추천합니다. SMS / CSV 로 잡힌 신규 거래 입력 직전에 호출 — 결과의 "
            "suggested[0].l_account 를 사용자에게 확인받고 공식 MCP add_entry 의 "
            "l_account 인자로 사용하세요. evidence 배열에 매칭된 과거 거래 샘플 포함."
        )
    )
    async def whooing_suggest_category(
        merchant: str,
        section_id: str | None = None,
        lookback_days: int = 180,
        min_similarity: float = 0.50,
        top_k: int = 3,
    ) -> dict:
        try:
            client, sid = await _ensure_client_and_section(section_id)
            return await suggest_category(
                client,
                merchant=merchant,
                section_id=sid,
                lookback_days=lookback_days,
                min_similarity=min_similarity,
                top_k=top_k,
            )
        except ToolError as e:
            return {"error": {"kind": e.kind, "message": e.message, **e.details}}

    @mcp.tool(
        description=(
            "SMS / 메일 / 텍스트를 _임시 큐_ 에 저장합니다 (후잉 자체 큐와 별개의 "
            "로컬 SQLite 큐). 사용자가 거래 입력을 즉시 못하고 나중에 정리할 때 "
            "사용. parsed dict 가 있으면 같이 저장 (whooing_parse_payment_sms "
            "결과를 그대로 넘기는 패턴)."
        )
    )
    async def whooing_enqueue_pending(
        text: str | None = None,
        parsed: dict | None = None,
        source: str = "manual",
        issuer: str | None = None,
        section_id: str | None = None,
        note: str | None = None,
    ) -> dict:
        try:
            return await enqueue_pending(
                text=text, parsed=parsed, source=source,
                issuer=issuer, section_id=section_id, note=note,
            )
        except ToolError as e:
            return {"error": {"kind": e.kind, "message": e.message, **e.details}}

    @mcp.tool(
        description=(
            "임시 큐의 미정리 항목 조회. source 로 'manual'|'sms'|'email' 필터, "
            "since 로 ISO8601 시각 이후 필터. 각 item 의 parsed 필드에 SMS 파서 "
            "결과 dict 가 있어 사용자에게 보여주기 좋다."
        )
    )
    async def whooing_list_pending(
        source: str | None = None,
        since: str | None = None,
        limit: int = 50,
    ) -> dict:
        try:
            return await list_pending(source=source, since=since, limit=limit)
        except ToolError as e:
            return {"error": {"kind": e.kind, "message": e.message, **e.details}}

    @mcp.tool(
        description=(
            "큐 항목을 후잉에 입력 완료한 뒤 큐에서 제거. 본 도구는 우리 자체 "
            "큐만 정리하며, 실제 후잉 add_entry 호출은 별도로 공식 MCP 의 "
            "add_entry 도구로 LLM 이 처리해야 한다."
        )
    )
    async def whooing_confirm_pending(
        pending_id: int,
        note: str | None = None,
    ) -> dict:
        try:
            return await confirm_pending(pending_id=pending_id, note=note)
        except ToolError as e:
            return {"error": {"kind": e.kind, "message": e.message, **e.details}}

    @mcp.tool(
        description=(
            "큐 항목을 _입력하지 않고_ 제거. 'confirm' 과 의미 구분 — 무시/취소/"
            "중복 같은 사유로 후잉에 안 넣을 항목."
        )
    )
    async def whooing_dismiss_pending(
        pending_id: int,
        reason: str | None = None,
    ) -> dict:
        try:
            return await dismiss_pending(pending_id=pending_id, reason=reason)
        except ToolError as e:
            return {"error": {"kind": e.kind, "message": e.message, **e.details}}

    # ─── Phase 2.2 (v0.2.0) 에서 다음 10 도구 제거 ─────────────────────────
    #   메모/해시태그: set_entry_note / get_entry_annotations / remove_entry_note /
    #                  list_hashtags / find_entries_by_hashtag
    #   첨부:          attach_file_to_entry / list_entry_attachments / remove_attachment
    #   import:        import_html_statement / import_pdf_statement
    # 모두 whooing-tui 가 owner. wrapper 는 SQLite read-only 로 audit/list 응답
    # augmentation (`local_annotations`, `local_attachments`) 에만 활용 (Phase 2.3+).

    @mcp.tool(
        description=(
            "후잉 거래를 영구 삭제합니다 (공식 MCP 의 entries-delete 호출 통과). "
            "재무 데이터 영구 삭제 — 'confirm=True' 필수 가드. entry_ids 는 list "
            "또는 단일 string. update_import_log=True (기본) 면 statement_import_log "
            "의 해당 entries 도 status='deleted' 로 동기화."
        )
    )
    async def whooing_delete_entries(
        entry_ids: str | list[str],
        section_id: str | None = None,
        confirm: bool = False,
        update_import_log: bool = True,
    ) -> dict:
        try:
            _, sid = await _ensure_client_and_section(section_id)
            return await delete_entries(
                entry_ids=entry_ids,
                section_id=sid,
                confirm=confirm,
                update_import_log=update_import_log,
            )
        except ToolError as e:
            return {"error": {"kind": e.kind, "message": e.message, **e.details}}

    @mcp.tool(
        description=(
            "월말 정산 합성 도구. 한 달치 거래의 (1) 거시 통계, (2) LLM 입력 "
            "거래(audit), (3) 중복 후보, (4) 카드명세서 정산(csv_path 또는 "
            "pdf_path 지정 시) 을 한 번에 묶어 반환합니다. next_actions 배열에 "
            "사용자 가이드 포함. yyyymm 은 'YYYYMM' 형식 (예: '202604')."
        )
    )
    async def whooing_monthly_close(
        yyyymm: str,
        section_id: str | None = None,
        csv_path: str | None = None,
        pdf_path: str | None = None,
        statement_issuer: str = "auto",
        duplicate_tolerance_days: int = 1,
        duplicate_min_similarity: float = 0.85,
        audit_marker: str = "[ai]",
    ) -> dict:
        try:
            client, sid = await _ensure_client_and_section(section_id)
            return await monthly_close(
                client,
                yyyymm=yyyymm,
                section_id=sid,
                csv_path=csv_path,
                pdf_path=pdf_path,
                statement_issuer=statement_issuer,
                duplicate_tolerance_days=duplicate_tolerance_days,
                duplicate_min_similarity=duplicate_min_similarity,
                audit_marker=audit_marker,
            )
        except ToolError as e:
            return {"error": {"kind": e.kind, "message": e.message, **e.details}}

    return mcp


# ---- main --------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        level=os.getenv("WHOOING_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,  # stdout 은 MCP framing 전용
    )
    _load_env()
    mcp = build_mcp()
    mcp.run()


if __name__ == "__main__":
    main()
