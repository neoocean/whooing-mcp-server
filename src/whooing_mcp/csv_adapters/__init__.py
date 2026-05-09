"""CSV adapter registry — 카드사 별 명세서 CSV 정규화."""

from __future__ import annotations

from collections.abc import Callable

from whooing_mcp.csv_adapters import kookmin_card, shinhan_card
from whooing_mcp.csv_adapters.base import CSVRow, DetectResult, read_csv

# (issuer_id, detect_fn, parse_fn)
_REGISTRY: list[tuple[str, Callable[[list[str]], float], Callable[[str], list[CSVRow]]]] = [
    ("shinhan_card", shinhan_card.score_header, shinhan_card.parse_csv),
    ("kookmin_card", kookmin_card.score_header, kookmin_card.parse_csv),
]


def known_issuers() -> list[str]:
    return [name for name, _, _ in _REGISTRY]


def detect(csv_path: str) -> DetectResult:
    """헤더만 보고 issuer 자동 탐지."""
    rows = read_csv(csv_path, max_rows=1)
    header = rows[0] if rows else []
    if not header:
        return DetectResult(
            detected_issuer=None,
            confidence=0.0,
            header_sample=[],
            column_mapping_proposed={},
        )

    best: tuple[str, float] | None = None
    for name, score_fn, _ in _REGISTRY:
        s = score_fn(header)
        if best is None or s > best[1]:
            best = (name, s)

    if best is None or best[1] < 0.4:
        return DetectResult(
            detected_issuer=None,
            confidence=best[1] if best else 0.0,
            header_sample=header,
            column_mapping_proposed={},
        )

    # 매핑 제안: adapter 모듈에서 가져옴
    issuer_name = best[0]
    mapping = _propose_mapping(issuer_name, header)
    return DetectResult(
        detected_issuer=issuer_name,
        confidence=best[1],
        header_sample=header,
        column_mapping_proposed=mapping,
    )


def parse(csv_path: str, issuer: str = "auto") -> tuple[str, list[CSVRow]]:
    """(issuer_used, rows) 반환. issuer='auto' 면 detect 후 그것으로 파싱."""
    if issuer == "auto":
        d = detect(csv_path)
        if d.detected_issuer is None:
            raise ValueError(
                f"CSV format not detected. header_sample={d.header_sample}. "
                f"supported issuers: {known_issuers()}"
            )
        issuer = d.detected_issuer

    for name, _, parse_fn in _REGISTRY:
        if name == issuer:
            return issuer, parse_fn(csv_path)

    raise ValueError(f"unknown issuer: {issuer!r}. supported: {known_issuers()}")


def _propose_mapping(issuer: str, header: list[str]) -> dict[str, str | None]:
    """헤더 키워드 기반 컬럼 매핑 제안 (adapter 모듈에 위임)."""
    if issuer == "shinhan_card":
        return shinhan_card.propose_mapping(header)
    if issuer == "kookmin_card":
        return kookmin_card.propose_mapping(header)
    return {}
