"""하나카드 보안메일 (CryptoJS AES) HTML adapter.

검증 (2026-05-09): 6자리 패스워드, UserFunc() 트리거, 평문 HTML 의 거래
테이블에서 (date, merchant, amount) 추출 후 dedup.

행 패턴 (plain-text 후 split('\\n') 기준 — nested table 정규화):
  03/15
  조원관광진흥주식회사
  41,200          ← 이용금액
  41,200          ← 결제금액 (할인 X 면 같음)
  03/15           ← 결제일

  03/15
  SKT통신요금할인받으신금액
  15,000          ← 이용금액 (양수)
  할인            ← 혜택 type
  -15,000         ← 혜택금액 (음수 = 차감)

  03/19           ← 외화 사용분
  PAYPAL ARLOTECHNOL
  30,476          ← 이용금액 (KRW 환산됨)
  30,476          ← 결제금액
  60              ← 수수료
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from whooing_mcp.csv_adapters.base import CSVRow
from whooing_mcp.html_adapters.base import (
    HtmlDecryptError,
    decrypt_html_with_playwright,
)

log = logging.getLogger(__name__)

ISSUER = "hanacard_secure_mail"


def is_match(head_text: str) -> bool:
    """첫 8KB 텍스트로 issuer 식별. encrypted HTML 라도 title/script 키워드는 노출됨."""
    return all(kw in head_text for kw in ["하나", "보안", "uni_func", "CryptoJS"]) \
        or ("하나카드" in head_text and "uni_func" in head_text)


def parse_html(html_path: str, password: str | None) -> list[CSVRow]:
    """복호화 → plain text → 거래 추출 → dedup."""
    if not password:
        raise HtmlDecryptError("hanacard_secure_mail: password 필수")

    decrypted_html = asyncio.get_event_loop().run_until_complete(
        decrypt_html_with_playwright(html_path, password)
    ) if not asyncio.get_event_loop().is_running() else None

    if decrypted_html is None:
        # 이미 async context 내 (예: tool 도구 호출) — sync 변환 어려움.
        # 본 함수는 sync 인터페이스라 caller 가 직접 비동기 호출 권장.
        raise HtmlDecryptError(
            "parse_html 은 sync 호출 전용. 도구에서는 parse_html_async 사용."
        )

    return _extract_rows_from_decrypted(decrypted_html)


async def parse_html_async(html_path: str, password: str) -> list[CSVRow]:
    """tool 에서 호출 (비동기). decrypt + parse 한 번에."""
    if not password:
        raise HtmlDecryptError("hanacard_secure_mail: password 필수")
    decrypted = await decrypt_html_with_playwright(html_path, password)
    return _extract_rows_from_decrypted(decrypted)


# ---- parsing helpers --------------------------------------------------


_DATE_PAT = re.compile(r"^\d{2}/\d{2}$")
_AMOUNT_PAT = re.compile(r"^-?\d{1,3}(?:,\d{3})+$")
_NUM_PAT = re.compile(r"^-?\d+$")


def _extract_rows_from_decrypted(html: str) -> list[CSVRow]:
    """복호화된 HTML 에서 거래 행 추출 + dedup."""
    soup = BeautifulSoup(html, "html.parser")
    for s in soup.find_all("script"):
        s.decompose()
    plain = soup.get_text("\n", strip=True)
    lines = plain.splitlines()

    raw_rows: list[CSVRow] = []
    i = 0
    current_year = datetime.now().year

    while i < len(lines):
        if not _DATE_PAT.match(lines[i].strip()):
            i += 1
            continue

        date_md = lines[i].strip()  # MM/DD
        merchant = lines[i + 1].strip() if i + 1 < len(lines) else ""
        amount_use_str = lines[i + 2].strip() if i + 2 < len(lines) else ""
        cell4 = lines[i + 3].strip() if i + 3 < len(lines) else ""
        cell5 = lines[i + 4].strip() if i + 4 < len(lines) else ""

        # validate
        if not (_AMOUNT_PAT.match(amount_use_str) or _NUM_PAT.match(amount_use_str)):
            i += 1
            continue
        if not merchant:
            i += 1
            continue

        amount_use = _parse_money(amount_use_str)

        # 결제금액 / 할인 케이스 분기
        # 일반 (cell4 = 같은 금액): final = amount_use
        # 할인 (cell4 = '할인', cell5 = -x): final = -x (혜택금액)
        # 외화 (cell4 = 같은 금액, cell5 = 수수료): final = amount_use, fee = cell5
        fee = 0
        if cell4 == "할인" and (cell5.startswith("-") or _AMOUNT_PAT.match(cell5)):
            # 혜택금액으로 final amount 결정
            final_amount = _parse_money(cell5)
        else:
            final_amount = amount_use
            # cell5 가 작은 숫자면 수수료 (외화 케이스)
            if cell5 and (_AMOUNT_PAT.match(cell5) or _NUM_PAT.match(cell5)):
                cell5_int = _parse_money(cell5)
                # 외화 수수료는 보통 < 1000원
                if 0 < cell5_int < 1000:
                    fee = cell5_int

        # MM/DD → YYYYMMDD
        try:
            month, day = date_md.split("/")
            date_yyyymmdd = f"{current_year:04d}{int(month):02d}{int(day):02d}"
            datetime.strptime(date_yyyymmdd, "%Y%m%d")  # validate
        except (ValueError, IndexError):
            i += 1
            continue

        raw_rows.append(CSVRow(
            date=date_yyyymmdd,
            amount=final_amount + fee,  # total
            merchant=merchant,
            raw={"date_md": date_md, "amount_use": amount_use, "fee": fee,
                 "cell4": cell4, "cell5": cell5},
        ))
        i += 4  # move past this row

    # dedup: HTML 이 같은 거래를 여러 섹션에 표시 → (date, amount, merchant) 키로 unique
    seen: set[tuple] = set()
    unique: list[CSVRow] = []
    for r in raw_rows:
        key = (r.date, r.amount, r.merchant[:20])
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    log.info("hanacard_secure_mail: %d raw → %d unique", len(raw_rows), len(unique))
    return unique


def _parse_money(s: str) -> int:
    """'15,000' / '-15,000' / '60' → int."""
    s = s.strip()
    sign = -1 if s.startswith("-") else 1
    digits = re.sub(r"[^\d]", "", s)
    if not digits:
        return 0
    return sign * int(digits)
