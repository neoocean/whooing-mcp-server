"""카카오뱅크 체크카드 결제 알림 파서.

지원 패턴:
  [Web발신]
  카카오뱅크 결제
  홍****님 6,200원
  05/09 14:23 스타벅스강남점
  잔액 1,234,567원
"""

from __future__ import annotations

import re

from whooing_mcp.parsers.sms.base import (
    ParseResult,
    parse_money_korean,
    yyyymmdd_from_md,
)

ISSUER = "kakaobank"
ACCOUNT_GUESS = "카카오뱅크"

_PAT = re.compile(
    r"카카오뱅크"
    r".{0,30}?"
    r"(?:결제|승인|출금)"
    r".{0,80}?"
    r"(?P<money>[\d,]+)\s*원"
    r".{0,80}?"
    r"(?P<md>\d{1,2}\s*[/.\-]\s*\d{1,2})"
    r"(?:\s+\d{1,2}:\d{1,2})?"
    r"\s*(?P<merchant>[^\n\r]+?)\s*$",
    re.DOTALL | re.MULTILINE,
)


def parse(text: str) -> ParseResult | None:
    if "카카오뱅크" not in text:
        return None
    m = _PAT.search(text)
    if not m:
        return None
    try:
        money = parse_money_korean(m.group("money"))
        entry_date = yyyymmdd_from_md(m.group("md"))
    except ValueError:
        return None

    merchant = re.sub(r"\s*잔액\s*[\d,]+\s*원.*$", "", m.group("merchant").strip()).strip()
    notes = ["통화: KRW"]
    confidence = 0.85 if "[Web발신]" in text or "[웹발신]" in text else 0.8

    return ParseResult(
        proposed_entry={
            "entry_date": entry_date,
            "money": money,
            "merchant": merchant,
            "direction": "expense",
            "suggested_l_account": None,
            "suggested_r_account": ACCOUNT_GUESS,
        },
        confidence=confidence,
        notes=notes,
        parser_used=f"{ISSUER}.v1",
    )
