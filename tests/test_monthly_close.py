"""tools/monthly_close.py — 합성 도구 회귀."""

from __future__ import annotations

from pathlib import Path

import pytest

from whooing_mcp.models import ToolError
from whooing_mcp.tools.monthly_close import _yyyymm_to_range, monthly_close

FIXTURES = Path(__file__).parent / "fixtures"


class FakeClient:
    def __init__(self, entries):
        self._entries = entries
        self.calls: list[dict] = []

    async def list_entries(self, *, section_id, start_date, end_date):
        self.calls.append({
            "section_id": section_id,
            "start_date": start_date,
            "end_date": end_date,
        })
        return [
            e for e in self._entries
            if start_date <= (e.get("entry_date") or "") <= end_date
        ]


def _e(eid, date, money, item="x", l_account="외식", memo=""):
    return {
        "entry_id": eid,
        "entry_date": date,
        "money": money,
        "item": item,
        "l_account": l_account,
        "memo": memo,
    }


# ---- _yyyymm_to_range ----------------------------------------------------


def test_yyyymm_to_range_april():
    assert _yyyymm_to_range("202604") == ("20260401", "20260430")


def test_yyyymm_to_range_february_leap():
    assert _yyyymm_to_range("202402") == ("20240201", "20240229")


def test_yyyymm_to_range_february_non_leap():
    assert _yyyymm_to_range("202502") == ("20250201", "20250228")


def test_yyyymm_to_range_invalid_format():
    with pytest.raises(ToolError):
        _yyyymm_to_range("2026-04")


def test_yyyymm_to_range_invalid_month():
    with pytest.raises(ToolError):
        _yyyymm_to_range("202613")


# ---- monthly_close ------------------------------------------------------


async def test_empty_month():
    out = await monthly_close(FakeClient([]), yyyymm="202604", section_id="s_FAKE")
    assert out["month"] == "202604"
    assert out["summary"]["entries_count"] == 0
    assert out["ai_entries"]["count"] == 0
    assert out["duplicates"]["pairs_count"] == 0
    assert out["reconcile"] is None
    assert "특이사항 없음" in out["next_actions"][0]


async def test_summary_groups_by_l_account():
    entries = [
        _e("a", "20260405", 6200, "스벅", "외식"),
        _e("b", "20260410", 12000, "마트", "외식"),
        _e("c", "20260415", 5000, "버스", "교통"),
    ]
    out = await monthly_close(FakeClient(entries), yyyymm="202604", section_id="s_FAKE")
    assert out["summary"]["entries_count"] == 3
    assert out["summary"]["total_money_sum"] == 23200
    assert out["summary"]["by_l_account"]["외식"] == 18200
    assert out["summary"]["by_l_account"]["교통"] == 5000


async def test_ai_marker_filter():
    entries = [
        _e("a", "20260405", 6200, "스벅", "외식", memo="[ai] 음성 입력"),
        _e("b", "20260410", 12000, "마트", "외식", memo=""),
        _e("c", "20260415", 5000, "[ai] item-marker 케이스", "교통"),
    ]
    out = await monthly_close(FakeClient(entries), yyyymm="202604", section_id="s_FAKE")
    assert out["ai_entries"]["count"] == 2
    assert out["ai_entries"]["marker_used"] == "[ai]"


async def test_duplicates_detected():
    entries = [
        _e("a", "20260405", 6200, "스타벅스 강남", "외식"),
        _e("b", "20260405", 6200, "스타벅스강남점", "외식"),
        _e("c", "20260410", 100, "다른가맹점", "외식"),
    ]
    out = await monthly_close(FakeClient(entries), yyyymm="202604", section_id="s_FAKE",
                              duplicate_min_similarity=0.7)
    assert out["duplicates"]["pairs_count"] >= 1


async def test_reconcile_csv_path():
    """csv_path 지정 시 reconcile 결과 포함."""
    entries = []  # 후잉 비어있음 → CSV 의 모든 거래가 missing
    out = await monthly_close(
        FakeClient(entries),
        yyyymm="202605",
        section_id="s_FAKE",
        csv_path=str(FIXTURES / "csv" / "shinhan_sample.csv"),
    )
    assert out["reconcile"] is not None
    assert out["reconcile"]["input_type"] == "csv"
    assert out["reconcile"]["summary"]["missing_in_whooing_count"] >= 1


async def test_reconcile_pdf_path():
    out = await monthly_close(
        FakeClient([]),
        yyyymm="202605",
        section_id="s_FAKE",
        pdf_path=str(FIXTURES / "pdf" / "shinhan_sample.pdf"),
    )
    assert out["reconcile"] is not None
    assert out["reconcile"]["input_type"] == "pdf"


async def test_csv_takes_precedence_over_pdf():
    """csv 와 pdf 둘 다 주면 csv 우선."""
    out = await monthly_close(
        FakeClient([]),
        yyyymm="202605",
        section_id="s_FAKE",
        csv_path=str(FIXTURES / "csv" / "shinhan_sample.csv"),
        pdf_path=str(FIXTURES / "pdf" / "shinhan_sample.pdf"),
    )
    assert out["reconcile"]["input_type"] == "csv"


async def test_relative_csv_rejected():
    with pytest.raises(ToolError):
        await monthly_close(
            FakeClient([]),
            yyyymm="202604",
            section_id="s_FAKE",
            csv_path="relative.csv",
        )


async def test_next_actions_summarize():
    """ai/dup/reconcile 모두 있을 때 next_actions 가 모두 언급."""
    entries = [
        _e("a", "20260405", 6200, "스벅", "외식", memo="[ai] x"),
        _e("b", "20260405", 6200, "스벅", "외식"),
    ]
    out = await monthly_close(
        FakeClient(entries),
        yyyymm="202604",
        section_id="s_FAKE",
        csv_path=str(FIXTURES / "csv" / "shinhan_sample.csv"),
        duplicate_min_similarity=0.7,
    )
    actions_str = " ".join(out["next_actions"])
    assert "[감사]" in actions_str
    assert "[중복]" in actions_str
    assert "[정산]" in actions_str


async def test_invalid_yyyymm():
    with pytest.raises(ToolError):
        await monthly_close(FakeClient([]), yyyymm="20260", section_id="s_FAKE")
