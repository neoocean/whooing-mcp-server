"""SMS / Push 결제 알림 파서 registry.

새 issuer 를 추가하려면:
  1. `parsers/sms/<issuer>.py` 에 `parse(text) -> ParseResult | None` 함수 정의
  2. 본 모듈의 `_REGISTRY` 에 추가
  3. `tests/test_sms_parsers.py` + `tests/fixtures/sms/<issuer>_*.txt` 추가
"""

from __future__ import annotations

from collections.abc import Callable

from whooing_mcp.parsers.sms import kookmin_card, shinhan_card
from whooing_mcp.parsers.sms.base import ParseResult

# (issuer_id, parse fn) — 순서 = auto-detect 시도 순서
_REGISTRY: list[tuple[str, Callable[[str], ParseResult | None]]] = [
    ("shinhan_card", shinhan_card.parse),
    ("kookmin_card", kookmin_card.parse),
]


def known_issuers() -> list[str]:
    return [name for name, _ in _REGISTRY]


def parse(text: str, issuer_hint: str = "auto") -> ParseResult | None:
    """힌트가 있으면 그 파서만, 없으면 모두 시도하고 가장 자신있는 결과 반환.

    매칭 없으면 None.
    """
    if issuer_hint and issuer_hint != "auto":
        for name, fn in _REGISTRY:
            if name == issuer_hint:
                return fn(text)
        return None

    best: ParseResult | None = None
    for _, fn in _REGISTRY:
        r = fn(text)
        if r is None:
            continue
        if best is None or r.confidence > best.confidence:
            best = r
    return best
