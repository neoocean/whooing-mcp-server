"""토스 (간편결제 / 토스카드) 결제 알림 파서.

지원 패턴 (토스는 카드사보다 간단한 알림 형식):
  [Web발신]
  토스 결제 완료
  6,200원
  스타벅스강남점
  05.09 14:23

또는:
  토스카드 승인 6,200원 05/09 14:23 스타벅스강남점
"""

from __future__ import annotations

import re

from whooing_mcp.parsers.sms.base import (
    ParseResult,
    parse_money_korean,
    yyyymmdd_from_md,
)

ISSUER = "toss"
ACCOUNT_GUESS = "토스"

# 토스는 '결제' 또는 '승인' 둘 다 가능
_PAT = re.compile(
    r"토스(?:카드)?"
    r".{0,30}?"
    r"(?:결제|승인)"
    r".{0,80}?"
    r"(?P<money>[\d,]+)\s*원"
    r".{0,80}?"
    r"(?P<md>\d{1,2}\s*[/.\-]\s*\d{1,2})"
    r"(?:\s+\d{1,2}:\d{1,2})?"
    r"\s*(?P<merchant>[^\n\r]+?)\s*$"
    r"|"
    # 다줄 패턴 (금액 → 가맹점 → 날짜 순)
    r"토스(?:카드)?.{0,30}?(?:결제|승인).{0,80}?"
    r"(?P<money2>[\d,]+)\s*원"
    r".{0,40}?"
    r"(?P<merchant2>[^\n\r]+?)"
    r".{0,40}?"
    r"(?P<md2>\d{1,2}\s*[/.\-]\s*\d{1,2})",
    re.DOTALL | re.MULTILINE,
)


def parse(text: str) -> ParseResult | None:
    if "토스" not in text:
        return None
    m = _PAT.search(text)
    if not m:
        return None

    money_s = m.group("money") or m.group("money2")
    md_s = m.group("md") or m.group("md2")
    merchant_s = m.group("merchant") or m.group("merchant2")
    if not (money_s and md_s and merchant_s):
        return None

    try:
        money = parse_money_korean(money_s)
        entry_date = yyyymmdd_from_md(md_s)
    except ValueError:
        return None

    merchant = re.sub(r"\s*잔액\s*[\d,]+\s*원.*$", "", merchant_s.strip()).strip()
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
