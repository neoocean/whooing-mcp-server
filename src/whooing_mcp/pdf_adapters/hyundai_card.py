"""현대카드 명세서 PDF adapter.

shinhan 과 거의 동일한 패턴. issuer 시그널만 다름.
"""

from __future__ import annotations

import re

from whooing_mcp.csv_adapters.base import CSVRow, parse_date, parse_money
from whooing_mcp.pdf_adapters.base import (
    extract_all_tables,
    extract_all_text_lines,
)

ISSUER = "hyundai_card"

_LINE_PAT = re.compile(
    r"^\s*(?P<date>\d{4}[./-]?\d{2}[./-]?\d{2})\s+"
    r"(?P<merchant>.+?)\s+"
    r"(?P<amount>-?[\d,]+)\s*(?:원)?\s*$"
)


def score_text(first_page_text: str) -> float:
    score = 0.0
    if "현대카드" in first_page_text:
        score += 0.6
    if "이용일자" in first_page_text or "거래일자" in first_page_text:
        score += 0.2
    if "이용금액" in first_page_text or "거래금액" in first_page_text:
        score += 0.2
    return min(1.0, score)


def parse_pdf(path: str) -> list[CSVRow]:
    rows: list[CSVRow] = []

    for table in extract_all_tables(path):
        if not table:
            continue
        header = [c.strip() if c else "" for c in table[0]]
        di = _find_col(header, ["이용일자", "이용일", "거래일자", "거래일"])
        ai = _find_col(header, ["이용금액", "거래금액", "승인금액"])
        mi = _find_col(header, ["가맹점명", "이용가맹점", "이용처"])
        if di is None or ai is None or mi is None:
            continue
        for r in table[1:]:
            if not r or all(not (c and c.strip()) for c in r):
                continue
            try:
                date = parse_date(r[di] or "")
                amount = parse_money(r[ai] or "")
                merchant = (r[mi] or "").strip()
                if not merchant:
                    continue
                rows.append(CSVRow(date=date, amount=amount, merchant=merchant,
                                   raw={h: (r[i] or "") for i, h in enumerate(header)}))
            except (ValueError, IndexError):
                continue

    if rows:
        return rows

    for line in extract_all_text_lines(path):
        m = _LINE_PAT.match(line)
        if not m:
            continue
        try:
            date = parse_date(m.group("date"))
            amount = parse_money(m.group("amount"))
            merchant = m.group("merchant").strip()
            rows.append(CSVRow(date=date, amount=amount, merchant=merchant,
                               raw={"line": line}))
        except (ValueError, IndexError):
            continue

    return rows


def _find_col(header: list[str], keywords: list[str]) -> int | None:
    norm = ["".join(h.split()).lower() for h in header]
    for kw in keywords:
        nk = "".join(kw.split()).lower()
        for i, h in enumerate(norm):
            if nk == h or nk in h:
                return i
    return None
