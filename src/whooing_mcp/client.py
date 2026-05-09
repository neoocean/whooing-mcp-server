"""후잉 REST API 클라이언트.

읽기 전용 (CRUD 는 공식 MCP — DESIGN §2). 노출 엔드포인트:
  GET /sections.json
  GET /entries.json?section_id=&start_date=&end_date=

DESIGN §4.2 (엔드포인트), §4.3 (응답 포맷), §4.4 (HTTP 매핑), §9 (rate limit).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from whooing_mcp.auth import WhooingAuth
from whooing_mcp.errors import map_response, sanitize_token
from whooing_mcp.models import ToolError

log = logging.getLogger(__name__)

DEFAULT_BASE = "https://whooing.com/api"

# DESIGN §9.2 — 분당 20 / 일 20,000 (공식). client-side 보수 throttle.
DEFAULT_RPM_CAP = 20
DEFAULT_RETRY_BACKOFF = (1.0, 2.0, 4.0, 8.0)  # 429 응답 시 max 4회


class WhooingClient:
    """thin httpx wrapper. 도구 입장에서는 dict/list 결과만 받는다."""

    def __init__(
        self,
        auth: WhooingAuth,
        base_url: str = DEFAULT_BASE,
        timeout: float = 10.0,
        rpm_cap: int = DEFAULT_RPM_CAP,
    ) -> None:
        self.auth = auth
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.rpm_cap = rpm_cap
        # 단일 프로세스 내의 요청 시각 sliding window.
        self._minute_window: list[float] = []
        self._lock = asyncio.Lock()

    # ---- rate limit ----------------------------------------------------

    async def _throttle(self) -> None:
        """분당 rpm_cap 초과 시 짧게 sleep. asyncio safe."""
        async with self._lock:
            now = time.monotonic()
            self._minute_window = [t for t in self._minute_window if now - t < 60]
            if len(self._minute_window) >= self.rpm_cap:
                oldest = self._minute_window[0]
                wait = 60.0 - (now - oldest) + 0.05
                log.debug(
                    "rate-limit throttle: %d req in last 60s, sleep %.2fs",
                    len(self._minute_window),
                    wait,
                )
                if wait > 0:
                    await asyncio.sleep(wait)
                # purge again 후 새 시각 기록
                now = time.monotonic()
                self._minute_window = [
                    t for t in self._minute_window if now - t < 60
                ]
            self._minute_window.append(now)

    # ---- HTTP ----------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        log.debug("GET %s params=%s auth=%s", url, params, sanitize_token(self.auth.token))

        last_error: ToolError | None = None
        for attempt, backoff in enumerate(DEFAULT_RETRY_BACKOFF):
            await self._throttle()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(url, headers=self.auth.headers(), params=params)
            try:
                return self._handle(r)
            except ToolError as e:
                if e.kind != "RATE_LIMIT":
                    raise
                # 429 → backoff 후 재시도. (402 일일은 retry 가치 없으므로 raise.)
                rest = e.details.get("rest_of_api")
                if rest is not None:
                    raise  # 일일 한도 — 재시도 안 함
                last_error = e
                log.warning(
                    "rate-limit 429 (attempt=%d), backoff %.1fs",
                    attempt + 1,
                    backoff,
                )
                await asyncio.sleep(backoff)

        # 모든 재시도 실패
        raise last_error or ToolError("RATE_LIMIT", "재시도 후에도 429")

    def _handle(self, r: httpx.Response) -> Any:
        """공식 응답 포맷 (DESIGN §4.3) 을 따라 results 추출 + 에러 매핑."""
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

        # 그 외는 errors 모듈에 위임
        raise map_response(code, msg, body, status=r.status_code)

    # ---- public API ---------------------------------------------------

    async def list_sections(self) -> list[dict[str, Any]]:
        results = await self._get("/sections.json")
        return self._normalize_collection(results, key="sections")

    async def list_entries(
        self,
        section_id: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """범위가 1년 초과면 자동 분할 후 병합 (DESIGN §4.2)."""
        from whooing_mcp.dates import split_yearly_ranges

        ranges = split_yearly_ranges(start_date, end_date)
        out: list[dict[str, Any]] = []
        for s, e in ranges:
            results = await self._get(
                "/entries.json",
                params={
                    "section_id": section_id,
                    "start_date": s,
                    "end_date": e,
                },
            )
            out.extend(self._normalize_collection(results, key="entries"))
        return out

    @staticmethod
    def _normalize_collection(results: Any, key: str) -> list[dict[str, Any]]:
        """후잉 응답이 list / {key: [...]} / {id: obj} 셋 다 가능 (DESIGN §4.2 추정).

        실 응답 모양은 CL #1 live smoke 에서 sections 는 list 확정. entries 는
        테스트 섹션 비어있어 미확정 — 첫 실 거래 후 검증.
        """
        if results is None:
            return []
        if isinstance(results, list):
            return results
        if isinstance(results, dict):
            if key in results and isinstance(results[key], list):
                return results[key]
            values = list(results.values())
            if values and all(isinstance(v, dict) for v in values):
                return values
        return []
