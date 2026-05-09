"""공식 후잉 MCP 서버 (https://whooing.com/mcp) HTTP JSON-RPC 클라이언트.

본 wrapper 가 직접 후잉 REST 를 두드리지 못하는 동작 (예: entries-delete —
REST DELETE 가 우리 호출에 응답 안 함, 2026-05-09 확인) 을 위임. 공식 MCP
서버는 stateless 라 init/initialized handshake 없이 `tools/call` 직접 가능.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_OFFICIAL_MCP_URL = "https://whooing.com/mcp"


class OfficialMcpError(Exception):
    """공식 MCP 호출 시 JSON-RPC error 또는 도구 실행 실패."""

    def __init__(self, message: str, *, code: int | None = None, data: Any = None) -> None:
        self.code = code
        self.data = data
        super().__init__(message)


class OfficialMcpClient:
    """최소 HTTP MCP 클라이언트 — tools/list + tools/call 만 노출."""

    def __init__(
        self,
        token: str,
        base_url: str = DEFAULT_OFFICIAL_MCP_URL,
        timeout: float = 30.0,
    ) -> None:
        self.token = token
        self.base_url = base_url
        self.timeout = timeout
        self._req_id = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

    async def list_tools(self) -> list[dict[str, Any]]:
        """공식 MCP 가 노출한 도구 목록."""
        body = await self._call("tools/list", {})
        return body.get("tools", []) or []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """단일 도구 호출. JSON-RPC error 면 OfficialMcpError raise.

        반환은 result.content 배열 (MCP spec) 또는 raw result. 호출자가 dict
        형태로 받음 (text content 는 .content[0].text 로 접근).
        """
        body = await self._call("tools/call", {"name": name, "arguments": arguments})
        # MCP tool result: {content: [{type: 'text'|'json', ...}], isError?: bool, structuredContent?: ...}
        if body.get("isError"):
            # extract human-readable error message
            content = body.get("content") or []
            msg_parts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    msg_parts.append(c.get("text", ""))
            raise OfficialMcpError(
                f"tool {name!r} returned isError: {' | '.join(msg_parts) or '(no message)'}",
                data=body,
            )
        return body

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        rpc_id = self._next_id()
        payload = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
        log.debug("MCP %s %s id=%d", self.base_url, method, rpc_id)

        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(self.base_url, headers=self._headers(), json=payload)

        try:
            body = r.json()
        except Exception:
            raise OfficialMcpError(
                f"non-JSON response from official MCP (status={r.status_code}): {r.text[:200]}"
            )

        if "error" in body:
            err = body["error"]
            raise OfficialMcpError(
                err.get("message", "unknown error"),
                code=err.get("code"),
                data=err.get("data"),
            )

        result = body.get("result")
        if result is None:
            raise OfficialMcpError(f"missing 'result' in MCP response: {body}")
        return result
