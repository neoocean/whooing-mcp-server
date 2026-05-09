"""tools/delete.py — confirm guard + official MCP delegation + log update."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from whooing_mcp.models import ToolError
from whooing_mcp.official_mcp import OfficialMcpError
from whooing_mcp.queue import open_db
from whooing_mcp.tools import delete as delete_mod


KST = ZoneInfo("Asia/Seoul")


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    monkeypatch.setenv("WHOOING_QUEUE_PATH", str(db))
    monkeypatch.setenv("WHOOING_AI_TOKEN", "__eyJh" + "x" * 100)
    yield db


def _seed_log(eids: list[str]) -> None:
    """statement_import_log 에 더미 entries 시드."""
    now = datetime.now(KST).isoformat(timespec="seconds")
    with open_db() as conn:
        for eid in eids:
            conn.execute(
                """INSERT INTO statement_import_log
                   (source_file, source_kind, statement_period_start, statement_period_end,
                    issuer, card_label, entry_date, merchant, original_amount, fee_amount,
                    total_amount, currency, foreign_amount, exchange_rate,
                    section_id, l_account_id, r_account_id,
                    whooing_entry_id, status, imported_at, error_message, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("/tmp/test.pdf", "pdf", "20260301", "20260331", "test", "TEST",
                 "20260315", f"item-{eid}", 1000, 0, 1000, "KRW", None, None,
                 "s_FAKE", "x50", "x80", eid, "inserted", now, None, None),
            )


# ---- confirm guard ----------------------------------------------------


async def test_confirm_required(tmp_db):
    with pytest.raises(ToolError) as ex:
        await delete_mod.delete_entries(entry_ids=["123"], section_id="s_FAKE")
    assert ex.value.kind == "USER_INPUT"
    assert "confirm=True" in ex.value.message


async def test_empty_entry_ids_rejected(tmp_db):
    with pytest.raises(ToolError):
        await delete_mod.delete_entries(entry_ids=[], section_id="s_FAKE", confirm=True)


async def test_blank_section_id_rejected(tmp_db):
    with pytest.raises(ToolError):
        await delete_mod.delete_entries(entry_ids=["1"], section_id="", confirm=True)


# ---- happy path with mocked official MCP -----------------------------


async def test_delete_single_entry(tmp_db, monkeypatch):
    """OfficialMcpClient.call_tool 을 mock — 모두 success."""
    captured = []

    async def fake_call(self, name, arguments):
        captured.append({"name": name, "arguments": arguments})
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    monkeypatch.setattr(
        "whooing_mcp.official_mcp.OfficialMcpClient.call_tool", fake_call
    )

    _seed_log(["1234"])
    out = await delete_mod.delete_entries(
        entry_ids="1234", section_id="s_FAKE", confirm=True
    )
    assert out["summary"]["deleted_count"] == 1
    assert out["deleted"] == ["1234"]
    assert out["failed"] == []
    assert out["log_updates"]["updated"] == 1
    # verify call shape
    assert captured == [{"name": "entries-delete",
                          "arguments": {"section_id": "s_FAKE", "entry_id": "1234"}}]


async def test_delete_multiple_entries(tmp_db, monkeypatch):
    async def fake_call(self, name, arguments):
        return {"content": [], "isError": False}

    monkeypatch.setattr(
        "whooing_mcp.official_mcp.OfficialMcpClient.call_tool", fake_call
    )

    _seed_log(["a1", "a2", "a3"])
    out = await delete_mod.delete_entries(
        entry_ids=["a1", "a2", "a3"], section_id="s_FAKE", confirm=True
    )
    assert out["summary"]["deleted_count"] == 3
    assert out["log_updates"]["updated"] == 3


async def test_delete_partial_failure(tmp_db, monkeypatch):
    """둘 중 하나는 official MCP 가 isError 반환."""
    call_count = 0

    async def fake_call(self, name, arguments):
        nonlocal call_count
        call_count += 1
        if arguments["entry_id"] == "bad":
            raise OfficialMcpError("거래를 찾을 수 없습니다", code=-1)
        return {"content": [], "isError": False}

    monkeypatch.setattr(
        "whooing_mcp.official_mcp.OfficialMcpClient.call_tool", fake_call
    )

    _seed_log(["good", "bad"])
    out = await delete_mod.delete_entries(
        entry_ids=["good", "bad"], section_id="s_FAKE", confirm=True
    )
    assert out["summary"]["deleted_count"] == 1
    assert out["summary"]["failed_count"] == 1
    assert out["deleted"] == ["good"]
    assert out["failed"][0]["entry_id"] == "bad"
    assert "찾을 수 없" in out["failed"][0]["error"]
    assert out["log_updates"]["updated"] == 1  # only 'good' updated


# ---- log update behaviors --------------------------------------------


async def test_log_update_disabled(tmp_db, monkeypatch):
    async def fake_call(self, name, arguments):
        return {"content": [], "isError": False}

    monkeypatch.setattr(
        "whooing_mcp.official_mcp.OfficialMcpClient.call_tool", fake_call
    )

    _seed_log(["x1"])
    out = await delete_mod.delete_entries(
        entry_ids=["x1"], section_id="s_FAKE", confirm=True,
        update_import_log=False,
    )
    # log row 의 status 그대로
    with open_db() as conn:
        r = conn.execute(
            "SELECT status FROM statement_import_log WHERE whooing_entry_id = 'x1'"
        ).fetchone()
    assert r["status"] == "inserted"  # not 'deleted' since update disabled


async def test_log_not_found_counted(tmp_db, monkeypatch):
    """delete success but no matching log row → log_updates.not_found++."""
    async def fake_call(self, name, arguments):
        return {"content": [], "isError": False}

    monkeypatch.setattr(
        "whooing_mcp.official_mcp.OfficialMcpClient.call_tool", fake_call
    )

    # NO seed
    out = await delete_mod.delete_entries(
        entry_ids=["unknown"], section_id="s_FAKE", confirm=True
    )
    assert out["log_updates"]["updated"] == 0
    assert out["log_updates"]["not_found"] == 1


# ---- input forms -----------------------------------------------------


async def test_string_input_normalized_to_list(tmp_db, monkeypatch):
    captured_count = 0

    async def fake_call(self, name, arguments):
        nonlocal captured_count
        captured_count += 1
        return {"content": [], "isError": False}

    monkeypatch.setattr(
        "whooing_mcp.official_mcp.OfficialMcpClient.call_tool", fake_call
    )

    out = await delete_mod.delete_entries(
        entry_ids="single", section_id="s_FAKE", confirm=True,
        update_import_log=False,
    )
    assert captured_count == 1
    assert out["deleted"] == ["single"]
