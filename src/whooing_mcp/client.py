"""후잉 REST API 클라이언트 — CL #1 read-only.

CL #1 은 audit 도구만 호출하므로 다음 두 엔드포인트만 노출:
  GET /sections.json
  GET /entries.json?section_id=&start_date=&end_date=

DESIGN §4.2 (엔드포인트), §4.3 (응답 포맷), §4.4 (HTTP 매핑) 참조.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from whooing_mcp.auth import WhooingAuth
from whooing_mcp.models import ToolError

log = logging.getLogger(__name__)

DEFAULT_BASE = "https://whooing.com/api"


class WhooingClient:
    """thin httpx wrapper. 호출자(도구)는 dict/list 결과만 받는다."""

    def __init__(
        self,
        auth: WhooingAuth,
        base_url: str = DEFAULT_BASE,
        timeout: float = 10.0,
    ) -> None:
        self.auth = auth
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        log.debug("GET %s params=%s", url, params)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(url, headers=self.auth.headers(), params=params)
        return self._handle(r)

    def _handle(self, r: httpx.Response) -> Any:
        """공식 응답 포맷 (DESIGN §4.3) 을 따라 results 추출 + 에러 매핑."""
        # 본문이 JSON 이 아닐 수 있으므로 방어
        try:
            body = r.json()
        except Exception:
            raise ToolError(
                "UPSTREAM",
                f"비-JSON 응답 (status={r.status_code})",
                status=r.status_code,
                snippet=r.text[:200],
            )

        rest = body.get("rest_of_api")
        if rest is not None:
            log.debug("rest_of_api=%s", rest)

        # 후잉은 본문 code 와 HTTP status 가 다를 수 있어 본문 우선
        code = body.get("code", r.status_code)
        msg = body.get("message", "") or ""
        results = body.get("results")

        if code == 200:
            return results if results is not None else body
        if code == 204:
            return [] if results is None else results
        if code in (401, 405):
            raise ToolError(
                "AUTH",
                "AI 토큰이 만료되었거나 거부되었습니다. "
                "후잉 → 사용자 > 계정 > 비밀번호 및 보안 에서 재발급 후 .env 갱신.",
                upstream_message=msg,
            )
        if code == 402:
            raise ToolError(
                "RATE_LIMIT",
                f"일일 한도 초과 (rest_of_api={rest})",
                rest_of_api=rest,
            )
        if code == 429:
            raise ToolError("RATE_LIMIT", "분당 한도 초과 (1분 대기 후 재시도)")
        if code == 400:
            raise ToolError(
                "USER_INPUT",
                msg or "잘못된 파라미터",
                error_parameters=body.get("error_parameters") or {},
            )
        if 500 <= code < 600:
            raise ToolError("UPSTREAM", f"후잉 서버 오류 (code={code}): {msg}")

        raise ToolError(
            "UPSTREAM",
            f"예상치 못한 응답 code={code} message={msg!r}",
            body_keys=list(body.keys()) if isinstance(body, dict) else None,
        )

    async def list_sections(self) -> list[dict[str, Any]]:
        results = await self._get("/sections.json")
        return self._normalize_collection(results, key="sections")

    async def list_entries(
        self,
        section_id: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        results = await self._get(
            "/entries.json",
            params={
                "section_id": section_id,
                "start_date": start_date,
                "end_date": end_date,
            },
        )
        return self._normalize_collection(results, key="entries")

    @staticmethod
    def _normalize_collection(results: Any, key: str) -> list[dict[str, Any]]:
        """후잉 응답이 list / {key: [...]} / {id: obj, id: obj} 셋 다 가능.

        실 응답 모양은 첫 live smoke 에서 확정 (CL #1 에 fixture 캡처).
        """
        if results is None:
            return []
        if isinstance(results, list):
            return results
        if isinstance(results, dict):
            if key in results and isinstance(results[key], list):
                return results[key]
            # {id: obj, id: obj} 매핑 — 값이 dict 면 그것을 사용
            values = list(results.values())
            if values and all(isinstance(v, dict) for v in values):
                return values
        return []
