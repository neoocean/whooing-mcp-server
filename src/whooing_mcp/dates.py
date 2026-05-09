"""KST 날짜 유틸 (DESIGN §4.5).

후잉의 모든 날짜는 KST 자정 기준 YYYYMMDD 문자열. 본 서버는 호스트의
시간대와 무관하게 항상 `Asia/Seoul` 강제.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    return datetime.now(KST)


def today_yyyymmdd() -> str:
    return now_kst().strftime("%Y%m%d")


def days_ago_yyyymmdd(days: int) -> str:
    """`days` 일 전의 KST 날짜를 YYYYMMDD 로 반환. days=0 이면 오늘."""
    if days < 0:
        raise ValueError(f"days must be >= 0, got {days}")
    return (now_kst() - timedelta(days=days)).strftime("%Y%m%d")


def parse_yyyymmdd(s: str) -> str:
    """문자열이 유효한 YYYYMMDD 형식인지 검증하고 그대로 반환.

    잘못된 입력에는 ValueError. 호출자(도구)가 ToolError 로 변환한다.
    """
    if not isinstance(s, str) or len(s) != 8 or not s.isdigit():
        raise ValueError(f"Expected YYYYMMDD (8자리 숫자), got: {s!r}")
    # 실제 달력상 유효한 날짜인지 (예: 20260230 차단)
    datetime.strptime(s, "%Y%m%d")
    return s
