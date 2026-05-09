"""KB국민카드 결제 알림 SMS / Push 파서.

지원 패턴:

  1) 표준 알림:
     KB국민카드 승인
     홍****님 6,200원 일시불
     05/09 14:23 스타벅스강남점
     누적 1,234,567원

  2) Push 한 줄:
     KB국민카드 승인 6,200원 일시불 05/09 14:23 스타벅스강남점
"""

from __future__ import annotations

import re

from whooing_mcp.parsers.sms.base import (
    ParseResult,
    parse_money_korean,
    yyyymmdd_from_md,
)

ISSUER = "kookmin_card"
ACCOUNT_GUESS = "국민카드"

_PAT = re.compile(
    r"KB\s*국민카드"
    r".{0,30}?"
    r"승인"
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
    if "국민카드" not in text:
        return None

    m = _PAT.search(text)
    if not m:
        return None

    money = parse_money_korean(m.group("money"))
    try:
        entry_date = yyyymmdd_from_md(m.group("md"))
    except ValueError:
        return None

    merchant = m.group("merchant").strip()
    merchant = re.sub(r"\s*누적\s*[\d,]+\s*원.*$", "", merchant).strip()

    billing = (m.group("billing") or "일시불").replace(" ", "")
    notes = []
    if billing != "일시불":
        notes.append(f"할부: {billing}")
    else:
        notes.append("할부 X (일시불)")
    notes.append("통화: KRW")

    return ParseResult(
        proposed_entry={
            "entry_date": entry_date,
            "money": money,
            "merchant": merchant,
            "direction": "expense",
            "suggested_l_account": None,
            "suggested_r_account": ACCOUNT_GUESS,
        },
        confidence=0.85,
        notes=notes,
        parser_used=f"{ISSUER}.v1",
    )
