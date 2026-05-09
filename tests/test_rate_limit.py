"""client.py rate limit (sliding window throttle).

실 후잉 호출 없이 monotonic clock + asyncio.sleep mocking 으로 검증.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from whooing_mcp.auth import WhooingAuth
from whooing_mcp.client import WhooingClient


def _make_client(rpm_cap: int = 3) -> WhooingClient:
    return WhooingClient(
        auth=WhooingAuth(token="__eyJh" + "x" * 100),
        base_url="https://example.com/api",
        timeout=1.0,
        rpm_cap=rpm_cap,
    )


async def test_throttle_no_wait_under_cap():
    """rpm_cap 미만 호출은 sleep 없음."""
    c = _make_client(rpm_cap=10)
    t0 = time.monotonic()
    for _ in range(5):
        await c._throttle()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.1, f"5 calls under cap should be instant, took {elapsed:.3f}s"


async def test_throttle_records_window():
    c = _make_client(rpm_cap=10)
    await c._throttle()
    await c._throttle()
    assert len(c._minute_window) == 2


async def test_throttle_purges_old_entries(monkeypatch):
    """60초 넘은 기록은 제거됨."""
    c = _make_client(rpm_cap=3)
    # 가짜 과거 기록 주입
    c._minute_window = [time.monotonic() - 70.0, time.monotonic() - 65.0]
    await c._throttle()
    # purge 후 새 기록 1개만 남음
    assert len(c._minute_window) == 1


async def test_throttle_waits_when_at_cap(monkeypatch):
    """cap 도달 시 가장 오래된 기록이 60초 넘을 때까지 대기."""
    c = _make_client(rpm_cap=2)

    # 최근 호출 2개 시뮬레이션 (29초 / 30초 전)
    now = time.monotonic()
    c._minute_window = [now - 29.0, now - 30.0]

    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)
        # 시뮬레이션이라 실제 대기 X — 대신 window 의 시각을 진짜로 옮긴 셈 치고
        # 다음 호출에서 purge 가 동작하도록 한다.
        for i in range(len(c._minute_window)):
            c._minute_window[i] -= s

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await c._throttle()
    assert len(sleeps) == 1
    # 60 - 30 = 30초 대기 기대 (+0.05 buffer)
    assert 29 < sleeps[0] < 32


async def test_throttle_concurrent_safe():
    """asyncio.gather 로 동시에 많이 호출해도 cap 을 어기지 않음."""
    c = _make_client(rpm_cap=5)
    await asyncio.gather(*(c._throttle() for _ in range(5)))
    # 5개 모두 기록되어야 (cap 내)
    assert len(c._minute_window) == 5
