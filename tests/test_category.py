"""tools/category.py — 카테고리 추천 알고리즘 회귀."""

from __future__ import annotations

import pytest

from whooing_mcp.models import ToolError
from whooing_mcp.tools.category import _vote, suggest_category


class FakeClient:
    def __init__(self, entries):
        self._entries = entries
        self.last_call: dict | None = None

    async def list_entries(self, *, section_id, start_date, end_date):
        self.last_call = {
            "section_id": section_id,
            "start_date": start_date,
            "end_date": end_date,
        }
        return list(self._entries)


def _e(eid, item, l_account, date="20260501", money=6200):
    return {
        "entry_id": eid,
        "entry_date": date,
        "money": money,
        "item": item,
        "l_account": l_account,
    }


# ---- _vote (pure function) -----------------------------------------------


def test_no_history_returns_empty():
    assert _vote([], "스타벅스", min_similarity=0.5, top_k=3) == []


def test_exact_match_high_confidence():
    entries = [
        _e("a", "스타벅스 강남점", "외식"),
        _e("b", "스타벅스 강남점", "외식"),
        _e("c", "스타벅스 강남점", "외식"),
    ]
    out = _vote(entries, "스타벅스 강남점", min_similarity=0.5, top_k=3)
    assert len(out) == 1
    assert out[0]["l_account"] == "외식"
    assert out[0]["confidence"] == 1.0
    assert out[0]["evidence_count"] == 3


def test_fuzzy_token_set_matches_different_branch():
    """'스타벅스 강남점' 학습 → '스타벅스 역삼점' query 도 매칭."""
    entries = [
        _e("a", "스타벅스 강남점", "외식"),
        _e("b", "스타벅스 강남점", "외식"),
    ]
    out = _vote(entries, "스타벅스 역삼점", min_similarity=0.5, top_k=3)
    assert len(out) == 1
    assert out[0]["l_account"] == "외식"
    assert out[0]["evidence_count"] == 2


def test_competing_categories_split_confidence():
    """같은 가맹점이 두 카테고리에 들어갔던 경우 vote 가 갈림."""
    entries = [
        _e("a", "GS25 합정점", "편의점", money=3000),
        _e("b", "GS25 합정점", "편의점", money=3000),
        _e("c", "GS25 합정점", "외식", money=3000),
    ]
    out = _vote(entries, "GS25 합정점", min_similarity=0.5, top_k=3)
    assert len(out) == 2
    # 편의점 이 더 우세
    assert out[0]["l_account"] == "편의점"
    assert out[0]["confidence"] > out[1]["confidence"]
    # 두 confidence 합 = 1.0
    assert round(sum(s["confidence"] for s in out), 3) == 1.0


def test_top_k_limits_results():
    entries = [
        _e("a", "한식집", "외식"),
        _e("b", "한식집", "음식"),
        _e("c", "한식집", "식비"),
        _e("d", "한식집", "기타"),
    ]
    out = _vote(entries, "한식집", min_similarity=0.5, top_k=2)
    assert len(out) == 2  # 4개 후보 중 상위 2개만


def test_min_similarity_filters_unrelated():
    entries = [
        _e("a", "전혀무관한가맹점이름이엄청길다", "기타"),
    ]
    out = _vote(entries, "스타벅스", min_similarity=0.7, top_k=3)
    assert out == []


def test_missing_l_account_skipped():
    """l_account 없는 항목은 학습 제외 (자산 간 이체 같은 경우)."""
    entries = [
        {"entry_id": "a", "entry_date": "20260501", "money": 100, "item": "스타벅스", "l_account": None},
        {"entry_id": "b", "entry_date": "20260501", "money": 100, "item": "스타벅스", "l_account": ""},
        {"entry_id": "c", "entry_date": "20260501", "money": 100, "item": "스타벅스", "l_account": "외식"},
    ]
    out = _vote(entries, "스타벅스", min_similarity=0.5, top_k=3)
    assert len(out) == 1
    assert out[0]["evidence_count"] == 1


def test_missing_item_skipped():
    entries = [
        {"entry_id": "a", "entry_date": "20260501", "money": 100, "item": "", "l_account": "외식"},
        {"entry_id": "b", "entry_date": "20260501", "money": 100, "item": "스타벅스", "l_account": "외식"},
    ]
    out = _vote(entries, "스타벅스", min_similarity=0.5, top_k=3)
    assert out[0]["evidence_count"] == 1


def test_evidence_truncated_to_top_3():
    """카테고리당 evidence 는 상위 3개만 (LLM 컨텍스트 절약)."""
    entries = [_e(f"e{i}", "스타벅스 강남점", "외식") for i in range(10)]
    out = _vote(entries, "스타벅스 강남점", min_similarity=0.5, top_k=3)
    assert out[0]["evidence_count"] == 10
    assert len(out[0]["evidence"]) == 3


def test_evidence_sorted_by_similarity_desc():
    entries = [
        _e("a", "완전다른가맹점이름이매우길다정말로", "외식"),  # 낮은 유사도
        _e("b", "스타벅스", "외식"),  # 높은 유사도
        _e("c", "스타벅스 강남점", "외식"),  # 가장 높은 유사도
    ]
    out = _vote(entries, "스타벅스 강남점", min_similarity=0.3, top_k=3)
    sims = [e["similarity"] for e in out[0]["evidence"]]
    assert sims == sorted(sims, reverse=True)


# ---- suggest_category (envelope + ToolError) ----------------------------


async def test_envelope_shape():
    entries = [
        _e("a", "스타벅스 강남점", "외식"),
        _e("b", "스타벅스 역삼점", "외식"),
    ]
    out = await suggest_category(
        FakeClient(entries),
        merchant="스타벅스 합정점",
        section_id="s_FAKE",
    )
    assert "suggested" in out
    assert out["merchant_searched"] == "스타벅스 합정점"
    assert out["section_id"] == "s_FAKE"
    assert out["lookback_days"] == 180
    assert out["scanned_total"] == 2
    assert out["match_count"] >= 2
    assert "note" in out


async def test_empty_history_note_guides_llm():
    out = await suggest_category(
        FakeClient([]),
        merchant="새 가맹점",
        section_id="s_FAKE",
    )
    assert out["suggested"] == []
    assert "사용자에게 카테고리를 직접" in out["note"]


async def test_history_but_no_match_note():
    entries = [_e("a", "전혀무관한_가맹점", "기타")]
    out = await suggest_category(
        FakeClient(entries),
        merchant="스타벅스",
        section_id="s_FAKE",
        min_similarity=0.9,
    )
    assert out["suggested"] == []
    assert "min_similarity" in out["note"] or "직접" in out["note"]


async def test_empty_merchant_rejected():
    with pytest.raises(ToolError) as ex:
        await suggest_category(
            FakeClient([]),
            merchant="   ",
            section_id="s_FAKE",
        )
    assert ex.value.kind == "USER_INPUT"


async def test_invalid_lookback_rejected():
    with pytest.raises(ToolError):
        await suggest_category(
            FakeClient([]),
            merchant="x",
            section_id="s_FAKE",
            lookback_days=0,
        )
    with pytest.raises(ToolError):
        await suggest_category(
            FakeClient([]),
            merchant="x",
            section_id="s_FAKE",
            lookback_days=731,
        )


async def test_invalid_top_k_rejected():
    with pytest.raises(ToolError):
        await suggest_category(
            FakeClient([]),
            merchant="x",
            section_id="s_FAKE",
            top_k=0,
        )


async def test_calls_correct_date_range():
    client = FakeClient([])
    await suggest_category(
        client,
        merchant="x",
        section_id="s_FAKE",
        lookback_days=30,
    )
    assert client.last_call["section_id"] == "s_FAKE"
    # date range 는 30일 (= today, 29 days ago)
    from whooing_mcp.dates import today_yyyymmdd
    assert client.last_call["end_date"] == today_yyyymmdd()
