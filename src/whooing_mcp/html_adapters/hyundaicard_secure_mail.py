"""현대카드 보안메일 (Yettiesoft vestmail / eval(atob(b_p)) 변형) HTML adapter.

검증 (2026-05-10): 6자리 패스워드, `doAction()` 트리거, 폼 `decForm` (id=password, name=p2).
복호화 후 `document.write(b)` 로 평문 HTML 본문 전체를 교체 → page.content() 로 capture.

행 패턴 (현대카드 명세서 — 2026-04 청구분 기준):
  결제일자       이용일자       이용가맹점        승인금액        결제금액
  2026/04/25     2026/03/31    스타벅스코리아      6,500           6,500
  2026/04/25     2026/04/02    이마트              42,300          42,300
  ...
  (할부 행은 결제금액 != 승인금액)

테이블 구조:
  - 청구금액 합계 / 일시불 거래내역 / 할부 거래내역 / 해외이용내역 등 여러 섹션이
    하나의 `<table>` 또는 인접 테이블에 분리됨.
  - 본 adapter 는 (date pattern, 가맹점, 금액) 트리플을 휴리스틱으로 추출 후
    (date, amount, merchant 첫 20자) 키로 dedup.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from whooing_mcp.csv_adapters.base import CSVRow
from whooing_mcp.html_adapters.base import (
    HtmlDecryptError,
    decrypt_html_with_playwright,
)

log = logging.getLogger(__name__)

ISSUER = "hyundaicard_secure_mail"


def is_match(head_text: str) -> bool:
    """첫 8KB 텍스트로 issuer 식별. encrypted HTML 라도 vestmail/현대 키워드 노출됨.

    구분 키 (hanacard 와 분리):
      - 'vestmail' 또는 'b_p' eval 패턴 (vestmail 기반)
      - 'hyundaicard' 도메인 또는 'HyundaiCard' 텍스트
      - 'doAction()' 함수 호출 (vestmail 폼 트리거)
    hanacard 는 'CryptoJS' + 'uni_func' 사용 — 둘과 겹치지 않음.
    """
    has_vestmail = "vestmail" in head_text or "b_p" in head_text
    has_hyundai = "hyundaicard" in head_text.lower() or "HyundaiCard" in head_text
    has_doaction = "doAction" in head_text
    has_cryptojs = "CryptoJS" in head_text  # hanacard 신호 — 있으면 hyundai 아님
    return (has_vestmail or has_doaction) and has_hyundai and not has_cryptojs


async def parse_html_async(html_path: str, password: str) -> list[CSVRow]:
    """tool 에서 호출 (비동기). decrypt + parse 한 번에."""
    if not password:
        raise HtmlDecryptError("hyundaicard_secure_mail: password 필수")
    # 현대카드 vestmail 폼은 #password 가 display:none 으로 시작하고 placeholder
    # 역할의 p2_temp 가 보임. fill 전에 strict 가시성 토글 필요.
    prefill_js = (
        "(function(){"
        "var p=document.getElementById('password');"
        "if(p){p.style.display='inline';}"
        "var t=document.getElementsByName('p2_temp');"
        "if(t&&t[0]){t[0].style.display='none';}"
        "})();"
    )
    decrypted = await decrypt_html_with_playwright(
        html_path,
        password,
        password_input_selector="#password",
        submit_function="doAction()",
        wait_after_submit_ms=3000,
        expected_alert_substrings=[
            "비밀번호 입력이 잘못",
            "비밀번호가 일치하지 않습니다",
            "incorrect",
        ],
        prefill_js=prefill_js,
    )
    return _extract_rows_from_decrypted(decrypted)


def parse_html(html_path: str, password: str | None) -> list[CSVRow]:
    """sync 인터페이스 — 본 adapter 는 async 만 지원."""
    raise HtmlDecryptError(
        "hyundaicard_secure_mail.parse_html 은 sync 미지원. "
        "tool 호출 시에는 parse_html_async 사용."
    )


# ---- parsing helpers --------------------------------------------------


# 현대카드 명세서 테이블의 날짜는 'YYYY/MM/DD' 또는 'MM/DD' 형태로 나타남.
_DATE_FULL_PAT = re.compile(r"^(\d{4})/(\d{2})/(\d{2})$")
_DATE_MD_PAT = re.compile(r"^(\d{2})/(\d{2})$")
_AMOUNT_PAT = re.compile(r"^-?\d{1,3}(?:,\d{3})+$")
_NUM_PAT = re.compile(r"^-?\d+$")


def _extract_rows_from_decrypted(html: str) -> list[CSVRow]:
    """복호화된 HTML 에서 거래 행 추출 + dedup.

    현대카드 명세서의 거래 테이블 컬럼 구조 (검증: 2026-04 청구분):
      [0] 이용일 (MM/DD)
      [1] 이용카드명
      [2] 이용가맹점          ← merchant
      [3] 이용금액            (할인 전)
      [4] 할부/회차
      [5] 적립/할인율(%)
      [6] 예상적립/할인       (음수 = 할인 차감)
      [7] 결제 원금           ← amount (할인 후 실 결제금액)
      [8] 결제 후 잔액
      [9] 수수료(이자)        ← fee (있으면 amount 에 더함)

    전략: 헤더 행 (`이용일|이용가맹점|결제 원금|수수료`) 가 매칭되는 테이블만
    추출 대상. 소계/총합계 행 (이용일 cell 비어있음) 자동 제외.
    """
    soup = BeautifulSoup(html, "html.parser")
    for s in soup.find_all("script"):
        s.decompose()
    for s in soup.find_all("style"):
        s.decompose()

    raw_rows: list[CSVRow] = []
    current_year = datetime.now().year

    for table in soup.find_all("table"):
        col_map = _identify_columns(table)
        if not col_map:
            continue
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            row = _parse_known_row(cells, col_map, current_year)
            if row:
                raw_rows.append(row)

    # fallback (table 매칭 실패 시 — 명세서 layout 변경 대응)
    if not raw_rows:
        log.warning("hyundaicard: 헤더 매칭 실패, plain-text fallback 사용")
        plain = soup.get_text("\n", strip=True)
        raw_rows = _extract_from_plain_text(plain, current_year)

    # ---- dedup ----
    seen: set[tuple] = set()
    unique: list[CSVRow] = []
    for r in raw_rows:
        key = (r.date, r.amount, r.merchant[:20])
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    log.info("hyundaicard_secure_mail: %d raw → %d unique", len(raw_rows), len(unique))
    return unique


def _identify_columns(table) -> dict[str, int] | None:
    """테이블 헤더 행 (보통 첫 <tr>) 에서 column index 매핑 반환.

    현대카드 거래 테이블의 헤더 키워드: 이용일 / 이용가맹점 / 결제 원금 / 수수료.
    매칭 실패 (= 거래 테이블 아님) 시 None.
    """
    rows = table.find_all("tr")
    if not rows:
        return None
    header_cells = [c.get_text(" ", strip=True) for c in rows[0].find_all(["td", "th"])]
    if len(header_cells) < 4:
        return None

    col_map: dict[str, int] = {}
    for idx, cell in enumerate(header_cells):
        norm = cell.replace(" ", "")
        if "이용일" == norm and "date" not in col_map:
            col_map["date"] = idx
        elif "이용가맹점" == norm and "merchant" not in col_map:
            col_map["merchant"] = idx
        elif norm in ("결제원금", "결제원금(원)") and "amount" not in col_map:
            col_map["amount"] = idx
        elif norm.startswith("수수료") and "fee" not in col_map:
            col_map["fee"] = idx

    # 필수: date, merchant, amount. fee 는 있으면 좋음.
    if {"date", "merchant", "amount"} <= col_map.keys():
        return col_map
    return None


def _parse_known_row(
    cells: list[str], col_map: dict[str, int], current_year: int,
) -> CSVRow | None:
    """헤더 매핑된 컬럼 인덱스 기준으로 row 파싱."""
    needed = max(col_map["date"], col_map["merchant"], col_map["amount"])
    if "fee" in col_map:
        needed = max(needed, col_map["fee"])
    if len(cells) <= needed:
        return None

    date_cell = cells[col_map["date"]].strip()
    if not date_cell:  # 소계/총합계 행
        return None
    date_yyyymmdd = _normalize_date(date_cell, current_year)
    if not date_yyyymmdd:
        return None  # 헤더 또는 비거래 행

    merchant = cells[col_map["merchant"]].strip()
    if not merchant:
        return None

    amount = _parse_money(cells[col_map["amount"]])
    fee = _parse_money(cells[col_map["fee"]]) if "fee" in col_map else 0
    total = amount + fee
    if total == 0:
        return None  # 결제원금 0 + 수수료 0 인 (할인 전부 흡수된) row 제외

    return CSVRow(
        date=date_yyyymmdd,
        amount=total,
        merchant=merchant,
        raw={"amount": amount, "fee": fee, "cells": cells},
    )


def _parse_table_row(cells: list[str], current_year: int) -> CSVRow | None:
    """<tr> 셀 리스트에서 (date, merchant, amount) 추출."""
    # 첫 cell 이 날짜인 경우만 처리 (header row 자동 제외).
    date_cell = cells[0]
    date_yyyymmdd = _normalize_date(date_cell, current_year)
    if not date_yyyymmdd:
        return None

    # 금액은 보통 마지막 또는 끝에서 두 번째 cell. 가맹점은 그 사이.
    # 우선순위: '결제금액' 으로 추정되는 마지막 amount.
    amount_idx = None
    for idx in range(len(cells) - 1, 0, -1):
        if _AMOUNT_PAT.match(cells[idx]) or (_NUM_PAT.match(cells[idx]) and len(cells[idx]) >= 3):
            amount_idx = idx
            break
    if amount_idx is None:
        return None

    final_amount = _parse_money(cells[amount_idx])
    if final_amount == 0:
        return None  # 합계행/구분선 등 0원 행 제외

    # merchant: 첫 번째 비-숫자 cell (날짜 다음).
    # 현대카드는 보통: [결제일, 이용일, 가맹점, 승인금액, 결제금액] 순.
    # 또는: [이용일, 가맹점, 금액].
    merchant = ""
    for c in cells[1:amount_idx]:
        if _normalize_date(c, current_year):
            # 또 다른 날짜 cell — 이게 '이용일자' 면 그쪽을 채택
            alt = _normalize_date(c, current_year)
            if alt:
                date_yyyymmdd = alt
            continue
        if _AMOUNT_PAT.match(c) or (_NUM_PAT.match(c) and len(c) >= 3):
            continue
        if not merchant:
            merchant = c
        else:
            # 추가 cell 은 무시 (예: 할부 회차)
            pass

    if not merchant:
        return None
    if len(merchant) > 100:  # 명백한 잘못된 행
        return None

    return CSVRow(
        date=date_yyyymmdd,
        amount=final_amount,
        merchant=merchant,
        raw={"cells": cells},
    )


def _extract_from_plain_text(plain: str, current_year: int) -> list[CSVRow]:
    """fallback — table 추출이 비었을 때 줄 단위 휴리스틱.

    hanacard 와 유사 패턴을 시도 — 다만 현대카드는 보통 table 추출이 성공함.
    """
    out: list[CSVRow] = []
    lines = plain.splitlines()
    i = 0
    while i < len(lines):
        date_yyyymmdd = _normalize_date(lines[i].strip(), current_year)
        if not date_yyyymmdd:
            i += 1
            continue
        # 다음 1~3 줄에서 merchant + amount 추출
        merchant = ""
        amount = 0
        consumed = 1
        for j in range(1, 4):
            if i + j >= len(lines):
                break
            cell = lines[i + j].strip()
            if not cell:
                continue
            if _AMOUNT_PAT.match(cell):
                amount = _parse_money(cell)
                consumed = j + 1
                break
            if not merchant and not _normalize_date(cell, current_year):
                merchant = cell
        if merchant and amount:
            out.append(CSVRow(
                date=date_yyyymmdd, amount=amount, merchant=merchant,
                raw={"src": "plaintext"},
            ))
            i += consumed
        else:
            i += 1
    return out


def _normalize_date(s: str, current_year: int) -> str | None:
    """날짜 문자열 → 'YYYYMMDD' 또는 None.

    지원 포맷: 'YYYY/MM/DD', 'YYYY-MM-DD', 'YYYY.MM.DD', 'MM/DD'.
    """
    s = s.strip()
    if not s:
        return None
    # YYYY/MM/DD 또는 변형
    m = re.match(r"^(\d{4})[./-](\d{1,2})[./-](\d{1,2})$", s)
    if m:
        y, mo, d = m.groups()
        try:
            datetime(int(y), int(mo), int(d))
            return f"{int(y):04d}{int(mo):02d}{int(d):02d}"
        except ValueError:
            return None
    # MM/DD
    m = _DATE_MD_PAT.match(s)
    if m:
        mo, d = m.groups()
        try:
            datetime(current_year, int(mo), int(d))
            return f"{current_year:04d}{int(mo):02d}{int(d):02d}"
        except ValueError:
            return None
    return None


def _parse_money(s: str) -> int:
    """'15,000' / '-15,000' / '60' → int."""
    s = s.strip()
    sign = -1 if s.startswith("-") else 1
    digits = re.sub(r"[^\d]", "", s)
    if not digits:
        return 0
    return sign * int(digits)
