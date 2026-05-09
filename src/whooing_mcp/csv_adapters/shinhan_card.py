"""신한카드 명세서 CSV adapter.

실 export 포맷은 시기마다 컬럼이 바뀐다. 본 adapter 는 헤더의
키워드(거래일자/이용일자/이용금액/가맹점명 등)로 컬럼을 식별한다.
"""

from __future__ import annotations

from whooing_mcp.csv_adapters.base import CSVRow, parse_date, parse_money, read_csv

ISSUER = "shinhan_card"

# 컬럼 키워드 (대소문자/공백 무시)
_DATE_KEYWORDS = ["거래일자", "이용일자", "사용일자", "승인일자", "거래일"]
_AMOUNT_KEYWORDS = ["이용금액", "거래금액", "승인금액", "결제금액"]
_MERCHANT_KEYWORDS = ["가맹점명", "이용가맹점", "가맹점", "이용처"]


def _norm(s: str) -> str:
    return "".join(s.split()).lower()


def _find_col(header: list[str], keywords: list[str]) -> int | None:
    norm_header = [_norm(h) for h in header]
    for kw in keywords:
        nk = _norm(kw)
        for i, h in enumerate(norm_header):
            if nk == h or nk in h:
                return i
    return None


def score_header(header: list[str]) -> float:
    """0.0 ~ 1.0. 헤더에 신한 명세서 키워드가 얼마나 있는지."""
    hits = 0
    if _find_col(header, _DATE_KEYWORDS) is not None:
        hits += 1
    if _find_col(header, _AMOUNT_KEYWORDS) is not None:
        hits += 1
    if _find_col(header, _MERCHANT_KEYWORDS) is not None:
        hits += 1
    # '신한' 단어가 헤더 어디에든 있으면 가산
    has_shinhan = any("신한" in h for h in header)
    base = hits / 3.0
    return min(1.0, base + (0.2 if has_shinhan else 0.0))


def propose_mapping(header: list[str]) -> dict[str, str | None]:
    def _name(idx: int | None) -> str | None:
        return header[idx] if idx is not None else None

    return {
        "date_col": _name(_find_col(header, _DATE_KEYWORDS)),
        "amount_col": _name(_find_col(header, _AMOUNT_KEYWORDS)),
        "merchant_col": _name(_find_col(header, _MERCHANT_KEYWORDS)),
    }


def parse_csv(path: str) -> list[CSVRow]:
    rows = read_csv(path)
    if not rows:
        return []
    header = rows[0]
    di = _find_col(header, _DATE_KEYWORDS)
    ai = _find_col(header, _AMOUNT_KEYWORDS)
    mi = _find_col(header, _MERCHANT_KEYWORDS)
    if di is None or ai is None or mi is None:
        raise ValueError(
            f"shinhan_card CSV: required columns missing. "
            f"detected: date={di}, amount={ai}, merchant={mi}. header={header}"
        )

    out: list[CSVRow] = []
    for r in rows[1:]:
        if not r or all(not c.strip() for c in r):
            continue
        try:
            date = parse_date(r[di])
            amount = parse_money(r[ai])
            merchant = r[mi].strip()
        except (ValueError, IndexError):
            continue
        out.append(
            CSVRow(
                date=date,
                amount=amount,
                merchant=merchant,
                raw=dict(zip(header, r)),
            )
        )
    return out
