"""tools/dedup.py — 알고리즘 단위 테스트."""

from __future__ import annotations

import pytest

from whooing_mcp.models import ToolError
from whooing_mcp.tools.dedup import _find_pairs, find_duplicates


class FakeClient:
    def __init__(self, entries):
        self._entries = entries

    async def list_entries(self, *, section_id, start_date, end_date):
        return list(self._entries)


# ---- _find_pairs (pure function) -----------------------------------------


def _e(eid, date, money, item, memo=""):
    return {
        "entry_id": eid,
        "entry_date": date,
        "money": money,
        "item": item,
        "memo": memo,
    }


def test_no_pairs_when_money_unique():
    entries = [_e("a", "20260509", 1000, "x"), _e("b", "20260509", 2000, "y")]
    assert _find_pairs(entries, tolerance_days=1, min_similarity=0.85) == []


def test_pair_same_money_same_day_similar_item():
    entries = [
        _e("a", "20260509", 6200, "스타벅스 강남점"),
        _e("b", "20260509", 6200, "스타벅스 강남"),
    ]
    pairs = _find_pairs(entries, tolerance_days=1, min_similarity=0.80)
    assert len(pairs) == 1
    assert {pairs[0]["entry_a"]["entry_id"], pairs[0]["entry_b"]["entry_id"]} == {"a", "b"}


def test_no_pair_when_date_too_far():
    entries = [
        _e("a", "20260501", 6200, "스타벅스"),
        _e("b", "20260509", 6200, "스타벅스"),
    ]
    pairs = _find_pairs(entries, tolerance_days=1, min_similarity=0.5)
    assert pairs == []


def test_no_pair_when_item_too_different():
    entries = [
        _e("a", "20260509", 6200, "스타벅스"),
        _e("b", "20260509", 6200, "전혀다른가맹점이름이엄청길게"),
    ]
    pairs = _find_pairs(entries, tolerance_days=1, min_similarity=0.85)
    assert pairs == []


def test_three_way_produces_three_pairs():
    entries = [
        _e("a", "20260509", 6200, "스타벅스"),
        _e("b", "20260509", 6200, "스타벅스"),
        _e("c", "20260509", 6200, "스타벅스"),
    ]
    pairs = _find_pairs(entries, tolerance_days=0, min_similarity=0.85)
    assert len(pairs) == 3  # ab, ac, bc


def test_money_string_handled():
    """후잉이 money 를 문자열로 보낼 가능성 방어."""
    entries = [
        {"entry_id": "a", "entry_date": "20260509", "money": "6200", "item": "x"},
        {"entry_id": "b", "entry_date": "20260509", "money": "6200", "item": "x"},
    ]
    pairs = _find_pairs(entries, tolerance_days=0, min_similarity=0.99)
    assert len(pairs) == 1


def test_missing_money_skipped():
    entries = [
        {"entry_id": "a", "entry_date": "20260509", "money": None, "item": "x"},
        {"entry_id": "b", "entry_date": "20260509", "money": None, "item": "x"},
    ]
    assert _find_pairs(entries, tolerance_days=0, min_similarity=0.5) == []


def test_why_contains_evidence():
    entries = [
        _e("a", "20260509", 6200, "스타벅스"),
        _e("b", "20260510", 6200, "스타벅스"),
    ]
    pairs = _find_pairs(entries, tolerance_days=1, min_similarity=0.85)
    why = pairs[0]["why"]
    assert any("same money" in w for w in why)
    assert any("similarity" in w for w in why)
    assert any("days apart" in w for w in why)


# ---- find_duplicates (with ToolError) ------------------------------------


async def test_invalid_dates_rejected():
    client = FakeClient([])
    with pytest.raises(ToolError) as ex:
        await find_duplicates(
            client, section_id="s_FAKE", start_date="2026-05-09", end_date="20260510"
        )
    assert ex.value.kind == "USER_INPUT"


async def test_start_after_end_rejected():
    client = FakeClient([])
    with pytest.raises(ToolError) as ex:
        await find_duplicates(
            client, section_id="s_FAKE", start_date="20260510", end_date="20260509"
        )
    assert ex.value.kind == "USER_INPUT"


async def test_invalid_tolerance_rejected():
    client = FakeClient([])
    with pytest.raises(ToolError):
        await find_duplicates(
            client,
            section_id="s_FAKE",
            start_date="20260501",
            end_date="20260509",
            tolerance_days=-1,
        )


async def test_returns_envelope():
    entries = [
        _e("a", "20260509", 6200, "스타벅스"),
        _e("b", "20260509", 6200, "스타벅스"),
    ]
    out = await find_duplicates(
        FakeClient(entries),
        section_id="s_FAKE",
        start_date="20260501",
        end_date="20260510",
    )
    assert out["total_checked"] == 2
    assert out["section_id"] == "s_FAKE"
    assert out["params"]["tolerance_days"] == 1
    assert len(out["pairs"]) == 1
