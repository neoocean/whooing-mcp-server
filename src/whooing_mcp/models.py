"""Pydantic models — CL #1 최소 집합.

후잉 응답 스키마는 엔드포인트별로 약간씩 다르고 일부 필드는 sparse 하므로
모든 모델에 `extra = "allow"` 를 둔다 (실 응답 누락 필드를 무시하지 않고
보존하기 위함).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class Section(BaseModel):
    """후잉 섹션 (가계부) — 최소 필드만."""

    model_config = ConfigDict(extra="allow")

    section_id: str
    title: str | None = None


class Entry(BaseModel):
    """후잉 거래 항목 — 최소 필드만 (CL #1 의 audit 도구 용도)."""

    model_config = ConfigDict(extra="allow")

    entry_id: str | None = None
    section_id: str | None = None
    entry_date: str | None = None  # YYYYMMDD
    money: int | None = None
    item: str = ""
    memo: str = ""


class ToolError(Exception):
    """도구 실행 중 발생하는 사용자 가시 에러.

    `kind` 는 LLM 이 분기 판단할 수 있도록 안정된 enum 문자열:
      - USER_INPUT  : 입력 파라미터 오류
      - AUTH        : 자격증명 만료/거부
      - RATE_LIMIT  : 분당/일일 한도
      - UPSTREAM    : 후잉 서버 오류 / 비예상 응답
      - INTERNAL    : 본 서버의 버그
    """

    def __init__(self, kind: str, message: str, **details: Any) -> None:
        self.kind = kind
        self.message = message
        self.details = details
        super().__init__(f"[{kind}] {message}")
