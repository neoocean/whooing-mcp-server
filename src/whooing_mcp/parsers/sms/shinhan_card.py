"""신한카드 결제 알림 SMS / Push 파서.

지원 패턴 (모두 합성 fixture 로 회귀 테스트됨):

  1) Web발신 다줄:
     [Web발신]
     신한카드(1234)승인
     홍길동님
     6,200원 일시불
     05/09 14:23
     스타벅스강남점

  2) Push 한 줄:
     신한카드 승인 ●●●●1234 6,200원 일시불 05/09 14:23 스타벅스강남점

  3) 할부:
     ... 6,200원 할부 3개월 ...

문구는 시간이 지나면 변경될 수 있다. 매칭 실패 시 None 반환 → tool 이
'matched parser' 0개 보고. fixture 에서 실 패턴 변화를 회귀로 잡는다.
"""

from __future__ import annotations

import re

from whooing_mcp.parsers.sms.base import (
    ParseResult,
    parse_money_korean,
    yyyymmdd_from_md,
)

ISSUER = "shinhan_card"
ACCOUNT_GUESS = "신한카드"

# 핵심 시그널: '신한카드' + '승인' + 금액 + 날짜 + 가맹점
# 다줄/한줄 모두 잡기 위해 DOTALL + 느슨한 사이 공백.
_PAT = re.compile(
    r"신한카드"
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
    if "신한카드" not in text:
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
    # 끝부분에 흔히 붙는 잡음 (누적금액, 카드번호 등) 제거 시도
    merchant = re.sub(r"\s*누적\s*[\d,]+\s*원.*$", "", merchant)
    merchant = merchant.strip()

    billing = (m.group("billing") or "일시불").replace(" ", "")
    notes = []
    if billing != "일시불":
        notes.append(f"할부: {billing}")
    else:
        notes.append("할부 X (일시불)")
    notes.append("통화: KRW")

    # 신뢰도: 다줄 패턴이 모두 매칭되면 높음
    confidence = 0.9 if "Web발신" in text or "[웹발신]" in text else 0.85

    return ParseResult(
        proposed_entry={
            "entry_date": entry_date,
            "money": money,
            "merchant": merchant,
            "direction": "expense",
            "suggested_l_account": None,  # 카테고리 추정은 v0.1 에 안 함
            "suggested_r_account": ACCOUNT_GUESS,
        },
        confidence=confidence,
        notes=notes,
        parser_used=f"{ISSUER}.v1",
    )
