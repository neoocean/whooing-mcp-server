"""SMS 파서 공통 타입 + helper."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from whooing_mcp.dates import now_kst


@dataclass
class ParseResult:
    """파서 1개의 매칭 결과.

    `proposed_entry` 는 후잉 add_entry 호출에 그대로 사용 가능한 dict 형태.
    suggested_l_account / suggested_r_account 가 None 이면 LLM 에게 보충
    질문하라고 신호.
    """

    proposed_entry: dict
    confidence: float  # 0.0 ~ 1.0
    notes: list[str] = field(default_factory=list)
    parser_used: str = ""


def yyyymmdd_from_md(md_str: str, *, sep: str = "/") -> str:
    """'05/09' / '5.9' / '5-9' → 'YYYYMMDD' (현재 KST 연도 기준).

    sep 은 정규식 캐릭터 클래스. 기본 '/'.
    """
    s = re.sub(rf"[\s{re.escape(sep)}]+", "/", md_str.strip())
    parts = re.split(r"[/.\-]", s)
    if len(parts) != 2:
        raise ValueError(f"Expected M/D, got {md_str!r}")
    month, day = int(parts[0]), int(parts[1])
    year = now_kst().year
    # validate
    return datetime(year, month, day).strftime("%Y%m%d")


def parse_money_korean(s: str) -> int:
    """'6,200' / '6,200원' / '6200원' → 6200."""
    digits = re.sub(r"[^\d]", "", s)
    if not digits:
        raise ValueError(f"no digits in {s!r}")
    return int(digits)
