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
from whooing_mcp.tools.dedup import find_duplicates
from whooing_mcp.tools.reconcile import csv_format_detect, reconcile_csv
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
    base = os.getenv("WHOOING_BASE_URL", "https://whooing.com/api")
    timeout = float(os.getenv("WHOOING_HTTP_TIMEOUT", "10"))
    auth = WhooingAuth(token=token)
    log.info("client built (auth=%r base=%s timeout=%.1f)", auth, base, timeout)
    section_id = (os.getenv("WHOOING_SECTION_ID") or "").strip() or None
    return WhooingClient(auth=auth, base_url=base, timeout=timeout), section_id


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
    mcp = FastMCP("whooing-extras")

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
