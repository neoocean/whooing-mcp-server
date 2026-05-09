"""whooing_parse_payment_sms — DESIGN v2 §6.1.

SMS / Push 알림 텍스트를 후잉 항목 후보로 변환. 순수 파싱, API 호출 X.

LLM 워크플로우:
  사용자가 알림 텍스트를 복사해 LLM 에 전달
  → LLM 이 본 도구 호출
  → 결과 dict 를 사용자에게 보여주고 확인
  → 확인되면 LLM 이 공식 MCP 의 add_entry 도구 호출
       (memo='[ai] ' + ... 권장 — audit 도구 추적용)
"""

from __future__ import annotations

from typing import Any

from whooing_mcp.models import ToolError
from whooing_mcp.parsers import sms as sms_parsers


async def parse_payment_sms(text: str, issuer_hint: str = "auto") -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ToolError("USER_INPUT", "text 가 비어있습니다.")

    if issuer_hint != "auto" and issuer_hint not in sms_parsers.known_issuers():
        raise ToolError(
            "USER_INPUT",
            f"지원하지 않는 issuer_hint: {issuer_hint!r}. "
            f"지원 목록: {sms_parsers.known_issuers()} 또는 'auto'.",
            supported=sms_parsers.known_issuers(),
        )

    result = sms_parsers.parse(text, issuer_hint=issuer_hint)
    if result is None:
        return {
            "proposed_entry": None,
            "confidence": 0.0,
            "notes": [
                f"매칭 파서 없음 (시도: {issuer_hint!r}). "
                f"지원 issuer: {sms_parsers.known_issuers()}. "
                "텍스트 일부를 수정해서 재시도하거나 LLM 이 직접 추출하세요.",
            ],
            "parser_used": None,
            "supported_issuers": sms_parsers.known_issuers(),
        }

    return {
        "proposed_entry": result.proposed_entry,
        "confidence": result.confidence,
        "notes": result.notes,
        "parser_used": result.parser_used,
        "next_step_hint": (
            "사용자 확인 후, 공식 MCP 의 add_entry 도구를 호출하세요. "
            "memo 첫 단어로 '[ai]' 를 붙이면 whooing_audit_recent_ai_entries "
            "로 추적 가능합니다."
        ),
    }
