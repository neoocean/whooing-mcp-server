"""AI 연동 토큰 헤더 빌더 + 마스크.

DESIGN §4.1 (인증), §8 (시크릿 관리), §13 (보안 가드).

후잉 공식 인증은 `X-API-Key: __eyJh...` 단일 헤더이다. 토큰 자체는 절대
로깅에 노출되어선 안 되며, 본 모듈의 객체는 `__repr__` / `__str__` 모두
마스크된 형태만 반환한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WhooingAuth:
    """AI 연동 토큰 1개를 보관하고 HTTP 헤더를 빌드한다."""

    token: str

    def headers(self) -> dict[str, str]:
        """X-API-Key 단일 헤더. 후잉 공식 권장 (whooing.com/api/docs)."""
        return {"X-API-Key": self.token}

    def __repr__(self) -> str:
        # __eyJh... 같은 토큰의 마지막 4자리만 hint 로 남기고 나머지는 마스크.
        # 토큰이 너무 짧으면 hint 도 안 남긴다 (오용 방지).
        if len(self.token) > 12:
            return f"WhooingAuth(token=***{self.token[-4:]}, len={len(self.token)})"
        return "WhooingAuth(token=***)"

    __str__ = __repr__
