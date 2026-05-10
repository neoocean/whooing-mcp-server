"""tools/pdf_import.py — PDF 카드명세서 import 도구 회귀."""

from __future__ import annotations

from pathlib import Path

import pytest

from whooing_core.csv_adapters.base import CSVRow
from whooing_mcp.models import ToolError
from whooing_mcp.tools import pdf_import as pdf_import_mod
from whooing_mcp.tools.pdf_import import import_pdf_statement


PDF_FIXTURES = Path(__file__).parent / "fixtures" / "pdf"
SHINHAN_PDF = str(PDF_FIXTURES / "shinhan_sample.pdf")


# ---- FakeClient ---------------------------------------------------------


class FakeClient:
    def __init__(self, entries=None, accounts=None):
        self._entries = entries or []
        self._accounts = accounts or {
            "expenses": [{"account_id": "x50", "title": "식비"},
                          {"account_id": "x77", "title": "통신"}],
            "liabilities": [{"account_id": "x80", "title": "[우진]하나카드"}],
            "assets": [], "income": [], "capital": [],
        }

    async def list_entries(self, *, section_id, start_date, end_date):
        return [
            e for e in self._entries
            if start_date <= str(e.get("entry_date", "")).split(".")[0] <= end_date
        ]

    async def list_accounts(self, section_id):
        return self._accounts


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    monkeypatch.setenv("WHOOING_QUEUE_PATH", str(db))
    monkeypatch.setenv("WHOOING_AI_TOKEN", "__eyJh" + "x" * 100)
    yield db


# ---- 입력 검증 ---------------------------------------------------------


async def test_relative_path_rejected(tmp_db):
    with pytest.raises(ToolError):
        await import_pdf_statement(
            FakeClient(), pdf_path="relative.pdf",
            section_id="s_FAKE", r_account_id="x80"
        )


async def test_missing_file_rejected(tmp_db):
    with pytest.raises(ToolError):
        await import_pdf_statement(
            FakeClient(), pdf_path="/tmp/__nonexistent__.pdf",
            section_id="s_FAKE", r_account_id="x80"
        )


async def test_missing_r_account_id_rejected(tmp_db):
    with pytest.raises(ToolError) as ex:
        await import_pdf_statement(
            FakeClient(), pdf_path=SHINHAN_PDF,
            section_id="s_FAKE", r_account_id=""
        )
    assert "r_account_id" in ex.value.message


async def test_insert_without_confirm_rejected(tmp_db):
    with pytest.raises(ToolError) as ex:
        await import_pdf_statement(
            FakeClient(), pdf_path=SHINHAN_PDF,
            section_id="s_FAKE", r_account_id="x80",
            dry_run=False, confirm_insert=False,
        )
    assert "confirm_insert" in ex.value.message


async def test_unknown_issuer_rejected(tmp_db):
    with pytest.raises(ToolError):
        await import_pdf_statement(
            FakeClient(), pdf_path=SHINHAN_PDF,
            section_id="s_FAKE", r_account_id="x80", issuer="lotte_card"
        )


# ---- dry_run (default) -------------------------------------------------


async def test_dry_run_returns_proposals(tmp_db):
    """ledger 비어있으면 PDF 의 모든 행이 'new_proposed' 로."""
    out = await import_pdf_statement(
        FakeClient(), pdf_path=SHINHAN_PDF,
        section_id="s_FAKE", r_account_id="x80",
        auto_categorize=False,  # suggest_category 호출 회피 (ledger empty)
    )
    assert out["dry_run"] is True
    assert out["adapter_used"] == "shinhan_card"
    # synthetic PDF 에는 3건
    assert out["summary"]["pdf_total"] == 3
    assert out["summary"]["new_proposed_count"] == 3
    assert out["summary"]["matched_existing_count"] == 0
    assert out["summary"]["new_inserted_count"] == 0
    # tracking log 에 dry_run 으로 기록됨
    assert len(out["tracking_log_ids"]) == 3
    # 각 proposal 의 fallback_l_account_id 는 x50
    for p in out["proposed"]:
        assert p["suggested_l_account_id"] == "x50"
        assert p["suggested_l_account_name"] == "식비"
        assert "memo" in p


async def test_dry_run_dedup_matches_ledger(tmp_db):
    """ledger 에 일부 매칭 entries 있으면 'matched_existing' 분류."""
    ledger = [
        # synthetic PDF 의 첫 행 (20260509 6200 스벅) 매칭
        {"entry_id": "ledger_1", "entry_date": "20260509", "money": 6200,
         "item": "스타벅스 강남", "memo": "", "l_account_id": "x50",
         "r_account_id": "x80"},
    ]
    out = await import_pdf_statement(
        FakeClient(entries=ledger),
        pdf_path=SHINHAN_PDF,
        section_id="s_FAKE", r_account_id="x80",
        auto_categorize=False,
    )
    assert out["summary"]["matched_existing_count"] == 1
    assert out["summary"]["new_proposed_count"] == 2
    assert out["matched_existing"][0]["ledger_entry"]["entry_id"] == "ledger_1"


# ---- insert 모드 ------------------------------------------------------


async def test_insert_calls_official_mcp(tmp_db, monkeypatch):
    """dry_run=False + confirm_insert=True → official MCP entries-create 호출."""
    captured = []

    async def fake_call(self, name, arguments):
        captured.append({"name": name, "args": arguments})
        # entries-create 응답 모방 (text content with JSON)
        return {
            "content": [{"type": "text",
                         "text": '{"entry_id": "9999' + str(len(captured)) + '"}'}],
            "isError": False,
        }

    monkeypatch.setattr(
        "whooing_mcp.official_mcp.OfficialMcpClient.call_tool", fake_call
    )

    out = await import_pdf_statement(
        FakeClient(),
        pdf_path=SHINHAN_PDF,
        section_id="s_FAKE", r_account_id="x80",
        card_label="VISA3698",
        auto_categorize=False,
        dry_run=False, confirm_insert=True,
    )
    assert out["summary"]["new_inserted_count"] == 3
    assert out["summary"]["failed_count"] == 0
    assert len(out["inserted"]) == 3
    # 각 호출 검증
    for c in captured:
        assert c["name"] == "entries-create"
        assert c["args"]["section_id"] == "s_FAKE"
        assert c["args"]["r_account_id"] == "x80"
        assert c["args"]["l_account_id"] == "x50"
        assert c["args"]["l_account"] == "expenses"
        assert c["args"]["r_account"] == "liabilities"
        assert "VISA3698" in c["args"]["memo"]


async def test_insert_partial_failure(tmp_db, monkeypatch):
    """일부 호출이 OfficialMcpError raise → failed 로 분류, 나머지는 inserted."""
    from whooing_mcp.official_mcp import OfficialMcpError
    call_count = 0

    async def fake_call(self, name, arguments):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OfficialMcpError("rate limited or something")
        return {"content": [{"type": "text", "text": f'{{"entry_id":"e{call_count}"}}'}]}

    monkeypatch.setattr(
        "whooing_mcp.official_mcp.OfficialMcpClient.call_tool", fake_call
    )

    out = await import_pdf_statement(
        FakeClient(), pdf_path=SHINHAN_PDF,
        section_id="s_FAKE", r_account_id="x80",
        auto_categorize=False, dry_run=False, confirm_insert=True,
    )
    assert out["summary"]["new_inserted_count"] == 2
    assert out["summary"]["failed_count"] == 1


# ---- helpers ----------------------------------------------------------


def test_extract_entry_id_from_text_json():
    result = {
        "content": [{"type": "text", "text": '{"entry_id": "12345"}'}],
    }
    assert pdf_import_mod._extract_entry_id(result) == "12345"


def test_extract_entry_id_from_results_array():
    result = {
        "content": [{"type": "text", "text": '{"results": [{"entry_id": "777"}]}'}],
    }
    assert pdf_import_mod._extract_entry_id(result) == "777"


def test_extract_entry_id_from_structuredContent():
    result = {"structuredContent": {"entry_id": "42"}}
    assert pdf_import_mod._extract_entry_id(result) == "42"


def test_extract_entry_id_none_when_missing():
    result = {"content": [{"type": "text", "text": "ok"}]}
    assert pdf_import_mod._extract_entry_id(result) is None


def test_widen_range_no_tolerance():
    assert pdf_import_mod._widen_range("20260301", "20260331", 0) == ("20260301", "20260331")


def test_widen_range_with_tolerance():
    s, e = pdf_import_mod._widen_range("20260301", "20260331", 2)
    assert s == "20260227"
    assert e == "20260402"


def test_build_account_map():
    accounts = {
        "expenses": [{"account_id": "x50", "title": "식비"}],
        "liabilities": [{"account_id": "x80", "title": "하나카드"}],
    }
    m = pdf_import_mod._build_account_map(accounts)
    assert m["x50"] == ("expenses", "식비")
    assert m["x80"] == ("liabilities", "하나카드")


def test_find_existing_match_simple():
    """date + money 정확 일치 → match."""
    class R:
        date = "20260509"
        amount = 6200
        merchant = "스벅"
        fee = 0
    ledger = [
        {"entry_id": "1", "entry_date": "20260509.0001", "money": 6200, "item": "스타벅스"},
        {"entry_id": "2", "entry_date": "20260509.0002", "money": 9999, "item": "다른거래"},
    ]
    m = pdf_import_mod._find_existing_match(R(), ledger, tolerance_days=1)
    assert m is not None
    assert m["entry_id"] == "1"


def test_find_existing_match_none_when_too_far():
    class R:
        date = "20260501"
        amount = 6200
        merchant = "x"
        fee = 0
    ledger = [{"entry_id": "1", "entry_date": "20260509", "money": 6200, "item": "x"}]
    m = pdf_import_mod._find_existing_match(R(), ledger, tolerance_days=1)
    assert m is None
