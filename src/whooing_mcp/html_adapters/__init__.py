"""HTML adapter registry — 카드사 보안메일 HTML 등의 명세서 형식.

pdf_adapters/ 와 평행 구조. 같은 CSVRow 타입을 반환해 reconcile/import
파이프라인 재사용.
"""

from __future__ import annotations

from collections.abc import Callable

from whooing_mcp.csv_adapters.base import CSVRow
from whooing_mcp.html_adapters import hanacard_secure_mail, hyundaicard_secure_mail
from whooing_mcp.html_adapters.base import HTMLDetectResult

# (issuer_id, is_match_fn, parse_fn)
#   is_match_fn(html_path, decrypted_text?) -> bool
#   parse_fn(html_path, password) -> list[CSVRow]
_REGISTRY: list[tuple[
    str,
    Callable[[str], bool],
    Callable[[str, str | None], list[CSVRow]],
]] = [
    ("hanacard_secure_mail",
     hanacard_secure_mail.is_match,
     hanacard_secure_mail.parse_html),
    ("hyundaicard_secure_mail",
     hyundaicard_secure_mail.is_match,
     hyundaicard_secure_mail.parse_html),
]


def known_issuers() -> list[str]:
    return [name for name, _, _ in _REGISTRY]


def detect(html_path: str) -> HTMLDetectResult:
    """파일 전체 (최대 1MB) 를 훑어 issuer 추정 — encrypted 라도 keyword 노출됨.

    이전 (v0.1.10): 8KB 만 봤음. 그러나 vestmail (현대카드) 의 마커
    (`vestmail`, `doAction`, `b_p`) 는 파일의 후반부에 등장하므로 1MB 까지
    읽는다. 명세서 HTML 은 보통 100~500KB 이므로 충분.
    """
    with open(html_path, encoding="utf-8", errors="replace") as f:
        head = f.read(1_048_576)  # 1 MB

    for name, is_match, _ in _REGISTRY:
        if is_match(head):
            return HTMLDetectResult(
                detected_issuer=name,
                confidence=0.9,
                head_excerpt=head[:300],
            )
    return HTMLDetectResult(
        detected_issuer=None, confidence=0.0, head_excerpt=head[:300],
    )


def parse(
    html_path: str,
    password: str | None = None,
    issuer: str = "auto",
) -> tuple[str, list[CSVRow]]:
    """(issuer_used, rows) 반환. auto 면 detect 후 그것으로."""
    if issuer == "auto":
        d = detect(html_path)
        if d.detected_issuer is None:
            raise ValueError(
                f"HTML format not detected. head excerpt: {d.head_excerpt!r}. "
                f"supported: {known_issuers()}"
            )
        issuer = d.detected_issuer

    for name, _, parse_fn in _REGISTRY:
        if name == issuer:
            return issuer, parse_fn(html_path, password)
    raise ValueError(f"unknown HTML issuer: {issuer!r}. supported: {known_issuers()}")
