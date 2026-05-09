"""우리은행 체크카드 결제 알림 파서.

지원 패턴:
  [Web발신]
  우리은행 체크카드 승인
  6,200원 일시불
  05/09 14:23
  스타벅스강남점
  잔액 1,234,567원
"""

from __future__ import annotations

import re

from whooing_mcp.parsers.sms.base import (
    ParseResult,
    parse_money_korean,
    yyyymmdd_from_md,
)

ISSUER = "woori_bank"
ACCOUNT_GUESS = "우리은행"

_PAT = re.compile(
    r"우리(?:은행)?"
    r".{0,30}?"
    r"(?:체크카드|카드)?"
    r"\s*(?:승인|결제|출금)"
    r".{0,80}?"
    r"(?P<money>[\d,]+)\s*원"
    r"\s*(?P<billing>일시불|할부\s*\d+\s*개월)?"
    r".{0,80}?"
    r"(?P<md>\d{1,2}\s*[/.\-]\s*\d{1,2})"
    r"(?:\s+\d{1,2}:\d{1,2})?"
    r"\s*(?P<merchant>[^\n\r]+?)\s*$",
    re.DOTALL | re.MULTILINE,
)


def parse(text: str) -> ParseResult | None:
    if "우리" not in text:
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
    billing = (m.group("billing") or "일시불").replace(" ", "")
    notes = ([f"할부: {billing}"] if billing != "일시불" else []) + ["통화: KRW"]
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
