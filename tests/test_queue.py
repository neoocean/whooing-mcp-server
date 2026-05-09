"""SQLite pending queue + tools 회귀 테스트."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from whooing_mcp.models import ToolError
from whooing_mcp.queue import (
    count,
    default_queue_path,
    delete,
    insert,
    list_items,
    open_db,
)
from whooing_mcp.tools.pending import (
    confirm_pending,
    dismiss_pending,
    enqueue_pending,
    list_pending,
)


@pytest.fixture
def tmp_queue(tmp_path, monkeypatch):
    """각 테스트마다 새 큐 db. WHOOING_QUEUE_PATH override."""
    db = tmp_path / "queue.db"
    monkeypatch.setenv("WHOOING_QUEUE_PATH", str(db))
    yield db


# ---- queue.py (low-level) ----------------------------------------------


def test_default_queue_path_uses_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WHOOING_QUEUE_PATH", str(tmp_path / "custom.db"))
    assert default_queue_path() == tmp_path / "custom.db"


def test_default_queue_path_xdg_when_no_env(monkeypatch):
    monkeypatch.delenv("WHOOING_QUEUE_PATH", raising=False)
    p = default_queue_path()
    assert p == Path.home() / ".local" / "share" / "whooing-mcp" / "queue.db"


def test_open_db_creates_schema_and_directory(tmp_queue):
    assert not tmp_queue.exists()
    with open_db():
        pass
    assert tmp_queue.exists()


def test_insert_and_count(tmp_queue):
    with open_db() as conn:
        out = insert(
            conn,
            source="sms",
            raw_text="신한카드 승인 6,200원",
            parsed={"merchant": "스벅", "money": 6200},
            issuer="shinhan_card",
            section_id="s_FAKE",
            note=None,
        )
    assert out["pending_id"] == 1
    assert "queued_at" in out
    with open_db() as conn:
        assert count(conn) == 1


def test_list_items_returns_parsed_dict(tmp_queue):
    with open_db() as conn:
        insert(
            conn, source="sms", raw_text="x",
            parsed={"a": 1, "b": "한글"},
            issuer=None, section_id=None, note=None,
        )
    with open_db() as conn:
        items = list_items(conn)
    assert len(items) == 1
    assert items[0]["parsed"] == {"a": 1, "b": "한글"}


def test_list_items_filters_by_source(tmp_queue):
    with open_db() as conn:
        insert(conn, source="sms", raw_text="a", parsed=None, issuer=None, section_id=None, note=None)
        insert(conn, source="manual", raw_text="b", parsed=None, issuer=None, section_id=None, note=None)
        insert(conn, source="email", raw_text="c", parsed=None, issuer=None, section_id=None, note=None)
    with open_db() as conn:
        sms_only = list_items(conn, source="sms")
    assert len(sms_only) == 1
    assert sms_only[0]["raw_text"] == "a"


def test_list_items_orders_desc(tmp_queue):
    with open_db() as conn:
        insert(conn, source="sms", raw_text="first", parsed=None, issuer=None, section_id=None, note=None)
    with open_db() as conn:
        insert(conn, source="sms", raw_text="second", parsed=None, issuer=None, section_id=None, note=None)
    with open_db() as conn:
        items = list_items(conn)
    # 가장 최근 (second) 가 먼저
    assert items[0]["raw_text"] == "second"


def test_delete_removes_and_returns_row(tmp_queue):
    with open_db() as conn:
        out = insert(conn, source="sms", raw_text="x", parsed={"y": 1}, issuer=None, section_id=None, note=None)
        pid = out["pending_id"]
    with open_db() as conn:
        deleted = delete(conn, pid)
    assert deleted is not None
    assert deleted["raw_text"] == "x"
    assert deleted["parsed"] == {"y": 1}
    with open_db() as conn:
        assert count(conn) == 0


def test_delete_nonexistent_returns_none(tmp_queue):
    with open_db() as conn:
        assert delete(conn, 999) is None


def test_schema_idempotent(tmp_queue):
    """open_db 를 여러 번 열어도 schema 가 깨지지 않음."""
    with open_db():
        pass
    with open_db():
        pass
    with open_db() as conn:
        assert count(conn) == 0


# ---- tools/pending (envelope) ------------------------------------------


async def test_enqueue_with_text(tmp_queue):
    out = await enqueue_pending(text="신한카드 승인 6,200원", source="sms")
    assert out["pending_id"] == 1
    assert out["queue_total"] == 1


async def test_enqueue_with_parsed(tmp_queue):
    parsed = {"merchant": "스타벅스", "money": 6200, "entry_date": "20260509"}
    out = await enqueue_pending(parsed=parsed, source="manual")
    assert out["pending_id"] == 1


async def test_enqueue_rejects_empty_input(tmp_queue):
    with pytest.raises(ToolError) as ex:
        await enqueue_pending()
    assert ex.value.kind == "USER_INPUT"


async def test_enqueue_rejects_bad_source(tmp_queue):
    with pytest.raises(ToolError):
        await enqueue_pending(text="x", source="weird")


async def test_list_returns_envelope(tmp_queue):
    await enqueue_pending(text="a", source="sms")
    await enqueue_pending(text="b", source="manual")
    out = await list_pending()
    assert out["returned"] == 2
    assert out["total_in_queue"] == 2
    assert "filters" in out


async def test_list_source_filter(tmp_queue):
    await enqueue_pending(text="a", source="sms")
    await enqueue_pending(text="b", source="manual")
    out = await list_pending(source="sms")
    assert out["returned"] == 1


async def test_confirm_removes_item(tmp_queue):
    e = await enqueue_pending(text="x", source="sms")
    pid = e["pending_id"]
    out = await confirm_pending(pending_id=pid)
    assert out["removed"] is True
    assert out["outcome"] == "confirmed"
    assert out["remaining_in_queue"] == 0


async def test_confirm_unknown_id_raises(tmp_queue):
    with pytest.raises(ToolError) as ex:
        await confirm_pending(pending_id=999)
    assert ex.value.kind == "USER_INPUT"


async def test_dismiss_removes_with_reason(tmp_queue):
    e = await enqueue_pending(text="x", source="sms")
    pid = e["pending_id"]
    out = await dismiss_pending(pending_id=pid, reason="테스트 중복")
    assert out["removed"] is True
    assert out["outcome"] == "dismissed"
    assert out["reason"] == "테스트 중복"


async def test_dismiss_unknown_id_raises(tmp_queue):
    with pytest.raises(ToolError):
        await dismiss_pending(pending_id=999)


async def test_full_lifecycle(tmp_queue):
    """enqueue → list → confirm 1건 → dismiss 1건 → 큐 비움."""
    await enqueue_pending(text="A", source="sms", note="첫 항목")
    await enqueue_pending(text="B", source="manual")
    await enqueue_pending(text="C", source="email")
    listed = await list_pending()
    assert listed["total_in_queue"] == 3

    # confirm 첫 번째
    first_id = listed["items"][-1]["id"]  # 가장 오래된 (asc 마지막) — 실제는 desc 정렬이라 마지막이 가장 오래됨
    await confirm_pending(pending_id=first_id)

    # dismiss 두 번째
    listed2 = await list_pending()
    if listed2["items"]:
        await dismiss_pending(pending_id=listed2["items"][0]["id"], reason="테스트")

    final = await list_pending()
    assert final["total_in_queue"] == 1
