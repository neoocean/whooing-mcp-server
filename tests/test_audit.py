"""tools/audit.py — synthetic fixture로 마커 매칭 동작 검증."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from whooing_mcp.tools.audit import audit_recent_ai_entries

FIXTURES = Path(__file__).parent / "fixtures"


class FakeClient:
    """list_entries 만 흉내내는 최소 클라이언트."""

    def __init__(self, entries: list[dict]) -> None:
        self._entries = entries
        self.last_call: dict | None = None

    async def list_entries(self, *, section_id: str, start_date: str, end_date: str) -> list[dict]:
        self.last_call = {
            "section_id": section_id,
            "start_date": start_date,
            "end_date": end_date,
        }
        return list(self._entries)


@pytest.fixture
def synthetic_entries() -> list[dict]:
    return json.loads((FIXTURES / "entries_sample.json").read_text("utf-8"))


async def test_filters_by_marker_in_memo(synthetic_entries: list[dict]) -> None:
    client = FakeClient(synthetic_entries)
    out = await audit_recent_ai_entries(client, section_id="s_FAKE_1", days=10)
    assert out["total"] == 2  # e_fake_001 (memo) + e_fake_003 (item)
    assert out["scanned_total"] == 3
    assert out["marker_used"] == "[ai]"


async def test_filters_by_marker_in_item(synthetic_entries: list[dict]) -> None:
    client = FakeClient(synthetic_entries)
    out = await audit_recent_ai_entries(client, section_id="s_FAKE_1", days=10)
    item_ids = {e["entry_id"] for e in out["entries"]}
    assert "e_fake_003" in item_ids  # item 으로 매칭


async def test_sorted_by_date_desc(synthetic_entries: list[dict]) -> None:
    client = FakeClient(synthetic_entries)
    out = await audit_recent_ai_entries(client, section_id="s_FAKE_1", days=10)
    dates = [e["entry_date"] for e in out["entries"]]
    assert dates == sorted(dates, reverse=True)


async def test_custom_marker(synthetic_entries: list[dict]) -> None:
    client = FakeClient(synthetic_entries)
    out = await audit_recent_ai_entries(
        client, section_id="s_FAKE_1", days=10, marker="[bot]"
    )
    assert out["total"] == 0  # 합성 데이터에 [bot] 없음
    assert out["marker_used"] == "[bot]"


async def test_invalid_days_rejected() -> None:
    from whooing_mcp.models import ToolError

    client = FakeClient([])
    with pytest.raises(ToolError) as exc:
        await audit_recent_ai_entries(client, section_id="s_FAKE_1", days=0)
    assert exc.value.kind == "USER_INPUT"


async def test_date_range_uses_today_as_end(synthetic_entries: list[dict]) -> None:
    from whooing_mcp.dates import today_yyyymmdd

    client = FakeClient(synthetic_entries)
    out = await audit_recent_ai_entries(client, section_id="s_FAKE_1", days=7)
    assert out["date_range"]["end"] == today_yyyymmdd()
    assert client.last_call["section_id"] == "s_FAKE_1"
