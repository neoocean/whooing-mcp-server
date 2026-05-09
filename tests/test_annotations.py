"""annotations.py + tools/annotations.py 회귀 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from whooing_mcp.annotations import (
    attach_annotations,
    delete_annotation,
    find_entry_ids_by_hashtag,
    get_annotation,
    get_annotations,
    list_all_hashtags,
    normalize_hashtag,
    parse_hashtag_input,
    upsert_annotation,
)
from whooing_mcp.models import ToolError
from whooing_mcp.queue import open_db
from whooing_mcp.tools.annotations import (
    find_entries_by_hashtag,
    get_entry_annotations,
    list_hashtags,
    remove_entry_note,
    set_entry_note,
)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    monkeypatch.setenv("WHOOING_QUEUE_PATH", str(db))
    yield db


# ---- normalize / parse_hashtag_input -----------------------------------


def test_normalize_strip_hash():
    assert normalize_hashtag("#식비") == "식비"


def test_normalize_strip_whitespace():
    assert normalize_hashtag("  food  ") == "food"


def test_normalize_rejects_internal_whitespace():
    with pytest.raises(ValueError):
        normalize_hashtag("work trip")


def test_normalize_rejects_empty():
    with pytest.raises(ValueError):
        normalize_hashtag("#")


def test_parse_hashtag_input_string():
    assert parse_hashtag_input("#식비 #출장 food") == ["식비", "출장", "food"]


def test_parse_hashtag_input_list():
    assert parse_hashtag_input(["식비", "#출장"]) == ["식비", "출장"]


def test_parse_hashtag_input_dedupe_preserve_order():
    assert parse_hashtag_input(["a", "b", "a", "c"]) == ["a", "b", "c"]


def test_parse_hashtag_input_none():
    assert parse_hashtag_input(None) == []


def test_parse_hashtag_input_comma_separated():
    assert parse_hashtag_input("a, b,c") == ["a", "b", "c"]


# ---- annotations.py CRUD ------------------------------------------------


def test_upsert_creates_new(tmp_db):
    with open_db() as conn:
        result = upsert_annotation(
            conn, entry_id="e123", note="첫 메모", hashtags=["식비", "테스트"]
        )
    assert result["entry_id"] == "e123"
    assert result["note"] == "첫 메모"
    assert sorted(result["hashtags"]) == ["식비", "테스트"]


def test_upsert_updates_existing(tmp_db):
    with open_db() as conn:
        upsert_annotation(conn, entry_id="e1", note="A", hashtags=["x"])
    with open_db() as conn:
        # note 만 변경 — hashtags 는 유지되어야
        upsert_annotation(conn, entry_id="e1", note="B")
    with open_db() as conn:
        out = get_annotation(conn, "e1")
    assert out["note"] == "B"
    assert out["hashtags"] == ["x"]


def test_upsert_replaces_hashtags_full(tmp_db):
    with open_db() as conn:
        upsert_annotation(conn, entry_id="e1", note="x", hashtags=["a", "b"])
    with open_db() as conn:
        upsert_annotation(conn, entry_id="e1", hashtags=["c"])
    with open_db() as conn:
        out = get_annotation(conn, "e1")
    assert out["hashtags"] == ["c"]


def test_upsert_empty_hashtags_clears_all(tmp_db):
    with open_db() as conn:
        upsert_annotation(conn, entry_id="e1", note="x", hashtags=["a", "b"])
    with open_db() as conn:
        upsert_annotation(conn, entry_id="e1", hashtags=[])
    with open_db() as conn:
        out = get_annotation(conn, "e1")
    assert out["hashtags"] == []


def test_get_annotations_batch(tmp_db):
    with open_db() as conn:
        upsert_annotation(conn, entry_id="e1", note="A", hashtags=["x"])
        upsert_annotation(conn, entry_id="e2", note="B", hashtags=["y"])
    with open_db() as conn:
        out = get_annotations(conn, ["e1", "e2", "e3"])
    assert set(out.keys()) == {"e1", "e2"}
    assert out["e1"]["hashtags"] == ["x"]


def test_delete_annotation_cascades_hashtags(tmp_db):
    with open_db() as conn:
        upsert_annotation(conn, entry_id="e1", note="x", hashtags=["a", "b"])
    with open_db() as conn:
        removed = delete_annotation(conn, "e1")
    assert removed is True
    with open_db() as conn:
        # 태그도 사라져야
        ids = find_entry_ids_by_hashtag(conn, "a")
    assert ids == []


def test_delete_nonexistent(tmp_db):
    with open_db() as conn:
        assert delete_annotation(conn, "nope") is False


# ---- list_hashtags + find_by_hashtag -----------------------------------


def test_list_hashtags_with_counts(tmp_db):
    with open_db() as conn:
        upsert_annotation(conn, entry_id="e1", note="", hashtags=["식비", "외식"])
        upsert_annotation(conn, entry_id="e2", note="", hashtags=["식비", "출장"])
        upsert_annotation(conn, entry_id="e3", note="", hashtags=["식비"])
    with open_db() as conn:
        tags = list_all_hashtags(conn)
    # 식비:3 가 첫 번째
    assert tags[0]["tag"] == "식비"
    assert tags[0]["count"] == 3


def test_list_hashtags_prefix_filter(tmp_db):
    with open_db() as conn:
        upsert_annotation(conn, entry_id="e1", note="", hashtags=["work-trip", "work-coffee"])
        upsert_annotation(conn, entry_id="e2", note="", hashtags=["food"])
    with open_db() as conn:
        tags = list_all_hashtags(conn, prefix="work")
    assert {t["tag"] for t in tags} == {"work-trip", "work-coffee"}


def test_find_entry_ids_by_hashtag(tmp_db):
    with open_db() as conn:
        upsert_annotation(conn, entry_id="e1", note="", hashtags=["a"])
        upsert_annotation(conn, entry_id="e2", note="", hashtags=["a", "b"])
        upsert_annotation(conn, entry_id="e3", note="", hashtags=["b"])
    with open_db() as conn:
        ids_a = find_entry_ids_by_hashtag(conn, "a")
        ids_b = find_entry_ids_by_hashtag(conn, "b")
    assert ids_a == ["e1", "e2"]
    assert ids_b == ["e2", "e3"]


# ---- attach_annotations ------------------------------------------------


def test_attach_annotations_no_local_data(tmp_db):
    entries = [{"entry_id": "e1", "money": 100}]
    out = attach_annotations(entries)
    assert out[0]["local_annotations"] is None


def test_attach_annotations_with_data(tmp_db):
    with open_db() as conn:
        upsert_annotation(conn, entry_id="e1", note="meta", hashtags=["x"])
    entries = [{"entry_id": "e1", "money": 100}]
    out = attach_annotations(entries)
    assert out[0]["local_annotations"] == {"note": "meta", "hashtags": ["x"]}


def test_attach_annotations_does_not_mutate_input(tmp_db):
    entries = [{"entry_id": "e1", "money": 100}]
    attach_annotations(entries)
    assert "local_annotations" not in entries[0]


def test_attach_annotations_empty_list(tmp_db):
    assert attach_annotations([]) == []


# ---- tools/annotations envelope ---------------------------------------


async def test_set_entry_note_creates(tmp_db):
    out = await set_entry_note(entry_id="e1", note="hello", hashtags="#식비")
    assert out["annotation"]["note"] == "hello"
    assert out["annotation"]["hashtags"] == ["식비"]


async def test_set_entry_note_rejects_both_none(tmp_db):
    with pytest.raises(ToolError):
        await set_entry_note(entry_id="e1")


async def test_set_entry_note_string_splits_whitespace(tmp_db):
    """문자열 입력은 공백으로 split — 4 tags."""
    out = await set_entry_note(entry_id="e1", hashtags="식비 출장 외식 커피")
    assert sorted(out["annotation"]["hashtags"]) == sorted(["식비", "출장", "외식", "커피"])


async def test_set_entry_note_list_rejects_internal_whitespace(tmp_db):
    """list 입력 시 각 아이템에 내부 공백 있으면 reject."""
    with pytest.raises(ToolError) as ex:
        await set_entry_note(entry_id="e1", hashtags=["invalid tag"])
    assert ex.value.kind == "USER_INPUT"


async def test_get_entry_annotations_envelope(tmp_db):
    await set_entry_note(entry_id="e1", note="x", hashtags=["a"])
    await set_entry_note(entry_id="e2", note="y")
    out = await get_entry_annotations(["e1", "e2", "e3"])
    assert out["found_count"] == 2
    assert out["queried_count"] == 3


async def test_remove_entry_note(tmp_db):
    await set_entry_note(entry_id="e1", note="x")
    out = await remove_entry_note("e1")
    assert out["removed"] is True


async def test_remove_entry_note_missing(tmp_db):
    out = await remove_entry_note("nonexistent")
    assert out["removed"] is False


async def test_list_hashtags_envelope(tmp_db):
    await set_entry_note(entry_id="e1", hashtags=["a", "b"])
    await set_entry_note(entry_id="e2", hashtags=["a"])
    out = await list_hashtags()
    assert out["total_unique"] == 2


# ---- find_entries_by_hashtag (역방향 with FakeClient) -----------------


class FakeClient:
    def __init__(self, entries):
        self._entries = entries

    async def list_entries(self, *, section_id, start_date, end_date):
        return [
            e for e in self._entries
            if start_date <= (e.get("entry_date") or "") <= end_date
        ]


async def test_find_entries_by_hashtag_round_trip(tmp_db):
    """로컬 태그 → 후잉 fetch → annotation 부착."""
    await set_entry_note(entry_id="e_real_1", note="첫 거래", hashtags=["식비"])
    await set_entry_note(entry_id="e_real_2", note="두번째", hashtags=["식비", "출장"])
    await set_entry_note(entry_id="e_real_3", note="세번째", hashtags=["취미"])

    # 후잉에 e_real_1, e_real_2 만 있다고 가정 (e_real_3 는 삭제됐거나 lookback 밖)
    from whooing_mcp.dates import today_yyyymmdd
    today = today_yyyymmdd()
    fake_remote = [
        {"entry_id": "e_real_1", "entry_date": today, "money": 6200, "item": "스벅"},
        {"entry_id": "e_real_2", "entry_date": today, "money": 35000, "item": "호텔"},
    ]

    out = await find_entries_by_hashtag(
        FakeClient(fake_remote), hashtag="식비", section_id="s_FAKE", lookback_days=30
    )
    assert out["total"] == 2
    assert out["hashtag_searched"] == "식비"
    # 부착된 annotation 확인
    first = out["entries"][0]
    assert "local_annotations" in first
    assert "식비" in first["local_annotations"]["hashtags"]


async def test_find_entries_by_hashtag_with_missing_in_remote(tmp_db):
    """로컬엔 있지만 후잉에 없는 entry_id 를 missing_in_remote_ids 로 보고."""
    await set_entry_note(entry_id="e1", hashtags=["x"])
    await set_entry_note(entry_id="e2", hashtags=["x"])  # 후잉엔 없음

    from whooing_mcp.dates import today_yyyymmdd
    today = today_yyyymmdd()
    fake_remote = [{"entry_id": "e1", "entry_date": today, "money": 100, "item": "a"}]

    out = await find_entries_by_hashtag(
        FakeClient(fake_remote), hashtag="x", section_id="s_FAKE"
    )
    assert out["total"] == 1
    assert out["missing_in_remote_count"] == 1
    assert "e2" in out["missing_in_remote_ids"]


async def test_find_entries_by_hashtag_no_local_data(tmp_db):
    out = await find_entries_by_hashtag(
        FakeClient([]), hashtag="없는태그", section_id="s_FAKE"
    )
    assert out["total"] == 0
    assert "로컬에 없습니다" in out["note"]


async def test_find_entries_invalid_hashtag(tmp_db):
    with pytest.raises(ToolError):
        await find_entries_by_hashtag(
            FakeClient([]), hashtag="  ", section_id="s_FAKE"
        )
