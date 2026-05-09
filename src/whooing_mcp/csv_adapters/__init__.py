"""CSV adapter registry — 카드사 별 명세서 CSV 정규화."""

from __future__ import annotations

from collections.abc import Callable

from whooing_mcp.csv_adapters import (
    hyundai_card,
    kookmin_card,
    samsung_card,
    shinhan_card,
)
from whooing_mcp.csv_adapters.base import (
    CSVRow,
    DetectResult,
    find_header_row,
    read_csv,
)

# (issuer_id, detect_fn, parse_fn) — 순서 = auto-detect 시도 순서
_REGISTRY: list[tuple[str, Callable[[list[str]], float], Callable[[str], list[CSVRow]]]] = [
    ("shinhan_card", shinhan_card.score_header, shinhan_card.parse_csv),
    ("kookmin_card", kookmin_card.score_header, kookmin_card.parse_csv),
    ("hyundai_card", hyundai_card.score_header, hyundai_card.parse_csv),
    ("samsung_card", samsung_card.score_header, samsung_card.parse_csv),
]


def known_issuers() -> list[str]:
    return [name for name, _, _ in _REGISTRY]


def detect(csv_path: str) -> DetectResult:
    """헤더 + 첫 metadata 줄 (제목 등) 을 보고 issuer 자동 탐지.

    카드사 CSV 는 흔히 첫 줄에 "현대카드 이용내역" 같은 제목이 오고 그 다음
    줄이 진짜 헤더. score_header 는 진짜 헤더 행을 받지만, 추가로 metadata
    rows 의 텍스트도 issuer 매칭 신호로 사용한다.
    """
    rows = read_csv(csv_path, max_rows=10)
    if not rows:
        return DetectResult(
            detected_issuer=None,
            confidence=0.0,
            header_sample=[],
            column_mapping_proposed={},
        )

    header_idx = find_header_row(rows)
    header = rows[header_idx]
    metadata_lines = " ".join(
        " ".join(c for c in row if c) for row in rows[:header_idx]
    )

    best: tuple[str, float] | None = None
    for name, score_fn, _ in _REGISTRY:
        s = score_fn(header)
        # metadata 에 issuer 명 (한글) 있으면 가산 — clamp 안 함 (tie-break 용).
        # 보고용 confidence 는 1.0 으로 cap.
        issuer_korean = _ISSUER_KOREAN.get(name, "")
        if issuer_korean and issuer_korean in metadata_lines:
            s += 0.5
        if best is None or s > best[1]:
            best = (name, s)

    if best is None or best[1] < 0.4:
        return DetectResult(
            detected_issuer=None,
            confidence=best[1] if best else 0.0,
            header_sample=header,
            column_mapping_proposed={},
        )

    issuer_name = best[0]
    mapping = _propose_mapping(issuer_name, header)
    return DetectResult(
        detected_issuer=issuer_name,
        confidence=min(1.0, best[1]),  # 보고용 cap
        header_sample=header,
        column_mapping_proposed=mapping,
    )


# 한국어 issuer 명 — metadata 매칭 시 가산 신호
_ISSUER_KOREAN: dict[str, str] = {
    "shinhan_card": "신한카드",
    "kookmin_card": "국민카드",
    "hyundai_card": "현대카드",
    "samsung_card": "삼성카드",
}


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
    if issuer == "hyundai_card":
        return hyundai_card.propose_mapping(header)
    if issuer == "samsung_card":
        return samsung_card.propose_mapping(header)
    return {}
