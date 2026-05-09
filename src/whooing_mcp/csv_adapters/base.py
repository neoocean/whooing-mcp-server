"""CSV adapter 공통 타입 + 인코딩 처리."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CSVRow:
    """카드사 명세서 1행을 후잉 매칭용으로 정규화."""

    date: str  # YYYYMMDD
    amount: int  # KRW (음수는 환불)
    merchant: str
    raw: dict[str, str] = field(default_factory=dict)  # 원본 컬럼 보존


@dataclass
class DetectResult:
    detected_issuer: str | None
    confidence: float
    header_sample: list[str]
    column_mapping_proposed: dict[str, str | None]


def read_csv(path: str, max_rows: int | None = None) -> list[list[str]]:
    """utf-8 우선, 실패 시 cp949 fallback (한국 카드사 export 의 흔한 인코딩)."""
    raw_bytes = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"cannot decode {path} as utf-8 or cp949")

    reader = csv.reader(text.splitlines())
    out: list[list[str]] = []
    for i, row in enumerate(reader):
        if max_rows is not None and i >= max_rows:
            break
        out.append(row)
    return out


def parse_money(s: str) -> int:
    """'6,200' / '6200원' / '-6,200' → int."""
    s = s.strip()
    sign = -1 if s.startswith("-") else 1
    digits = re.sub(r"[^\d]", "", s)
    if not digits:
        raise ValueError(f"no digits in {s!r}")
    return sign * int(digits)


def parse_date(s: str) -> str:
    """'2026-05-09' / '2026/05/09' / '20260509' / '26.05.09' → 'YYYYMMDD'."""
    s = s.strip()
    digits = re.sub(r"[^\d]", "", s)
    if len(digits) == 8:
        # YYYYMMDD
        datetime.strptime(digits, "%Y%m%d")
        return digits
    if len(digits) == 6:
        # YYMMDD → 2YYMMDD (가정: 2000년대)
        full = "20" + digits
        datetime.strptime(full, "%Y%m%d")
        return full
    raise ValueError(f"unrecognized date format: {s!r}")
