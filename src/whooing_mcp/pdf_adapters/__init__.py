"""PDF adapter registry — 카드사 명세서 PDF → CSVRow 정규화.

CSV adapter 와 동일한 CSVRow 타입을 반환해, reconcile 알고리즘을 그대로
재사용한다. 차이는 입력 매체 (PDF) 와 추출 방식 (pdfplumber) 뿐.
"""

from __future__ import annotations

from collections.abc import Callable

from whooing_mcp.csv_adapters.base import CSVRow
from whooing_mcp.pdf_adapters import hyundai_card, shinhan_card
from whooing_mcp.pdf_adapters.base import (
    PDFDetectResult,
    extract_first_page_text,
)

# (issuer_id, score_text_fn, parse_fn)
_REGISTRY: list[tuple[
    str,
    Callable[[str], float],          # 첫 페이지 텍스트로 issuer 추정 점수
    Callable[[str], list[CSVRow]],   # PDF path → CSVRow 리스트
]] = [
    ("shinhan_card", shinhan_card.score_text, shinhan_card.parse_pdf),
    ("hyundai_card", hyundai_card.score_text, hyundai_card.parse_pdf),
]


def known_issuers() -> list[str]:
    return [name for name, _, _ in _REGISTRY]


def detect(pdf_path: str) -> PDFDetectResult:
    """첫 페이지 텍스트로 issuer 추정."""
    try:
        text = extract_first_page_text(pdf_path)
    except Exception as e:
        return PDFDetectResult(
            detected_issuer=None,
            confidence=0.0,
            first_page_excerpt=f"<error: {e}>",
        )

    excerpt = text[:300]
    best: tuple[str, float] | None = None
    for name, score_fn, _ in _REGISTRY:
        s = score_fn(text)
        if best is None or s > best[1]:
            best = (name, s)

    if best is None or best[1] < 0.4:
        return PDFDetectResult(
            detected_issuer=None,
            confidence=best[1] if best else 0.0,
            first_page_excerpt=excerpt,
        )

    return PDFDetectResult(
        detected_issuer=best[0],
        confidence=best[1],
        first_page_excerpt=excerpt,
    )


def parse(pdf_path: str, issuer: str = "auto") -> tuple[str, list[CSVRow]]:
    """(issuer_used, rows) 반환. auto 면 detect 후 그것으로 파싱."""
    if issuer == "auto":
        d = detect(pdf_path)
        if d.detected_issuer is None:
            raise ValueError(
                f"PDF format not detected. excerpt={d.first_page_excerpt!r}. "
                f"supported issuers: {known_issuers()}"
            )
        issuer = d.detected_issuer

    for name, _, parse_fn in _REGISTRY:
        if name == issuer:
            return issuer, parse_fn(pdf_path)
    raise ValueError(f"unknown PDF issuer: {issuer!r}. supported: {known_issuers()}")
