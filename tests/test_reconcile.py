"""tools/reconcile.py — reconcile + format_detect tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from whooing_mcp.models import ToolError
from whooing_mcp.tools.reconcile import csv_format_detect, reconcile_csv

FIXTURES = Path(__file__).parent / "fixtures" / "csv"


class FakeClient:
    def __init__(self, entries):
        self._entries = entries

    async def list_entries(self, *, section_id, start_date, end_date):
        return [
            e for e in self._entries
            if start_date <= (e.get("entry_date") or "") <= end_date
        ]


# ---- csv_format_detect ---------------------------------------------------


async def test_format_detect_shinhan():
    out = await csv_format_detect(str(FIXTURES / "shinhan_sample.csv"))
    assert out["detected_issuer"] == "shinhan_card"
    assert out["confidence"] > 0.5
    assert "supported_issuers" in out


async def test_format_detect_relative_path_rejected():
    with pytest.raises(ToolError) as ex:
        await csv_format_detect("relative/path.csv")
    assert ex.value.kind == "USER_INPUT"


async def test_format_detect_missing_file_rejected():
    with pytest.raises(ToolError):
        await csv_format_detect("/tmp/__nonexistent_whooing_test__.csv")


# ---- reconcile_csv -------------------------------------------------------


async def test_reconcile_all_matched():
    """후잉에 모든 CSV 거래가 동일하게 있으면 matched=5, missing=0, extra=0."""
    entries = [
        {"entry_id": "w1", "entry_date": "20260509", "money": 6200, "item": "스타벅스 강남"},
        {"entry_id": "w2", "entry_date": "20260508", "money": 12500, "item": "GS25 테스트"},
        {"entry_id": "w3", "entry_date": "20260507", "money": 25000, "item": "합성마트"},
        {"entry_id": "w4", "entry_date": "20260506", "money": 350000, "item": "가짜전자"},
        {"entry_id": "w5", "entry_date": "20260505", "money": 15000, "item": "더미음식점"},
    ]
    out = await reconcile_csv(
        FakeClient(entries),
        csv_path=str(FIXTURES / "shinhan_sample.csv"),
        section_id="s_FAKE",
    )
    assert out["adapter_used"] == "shinhan_card"
    assert out["summary"]["csv_total"] == 5
    assert out["summary"]["matched_count"] == 5
    assert out["summary"]["missing_in_whooing_count"] == 0
    assert out["summary"]["extra_in_whooing_count"] == 0


async def test_reconcile_missing_in_whooing():
    """후잉이 비어있으면 모두 missing 으로 보고."""
    out = await reconcile_csv(
        FakeClient([]),
        csv_path=str(FIXTURES / "shinhan_sample.csv"),
        section_id="s_FAKE",
    )
    assert out["summary"]["matched_count"] == 0
    assert out["summary"]["missing_in_whooing_count"] == 5
    # missing_in_whooing 의 첫 항목이 CSVRow dict 형태
    first = out["missing_in_whooing"][0]
    assert "date" in first and "amount" in first and "merchant" in first


async def test_reconcile_extra_in_whooing():
    """CSV 가 비어있고 후잉에만 있으면 extra 로."""
    entries = [
        {"entry_id": "w1", "entry_date": "20260509", "money": 6200, "item": "스타벅스"},
    ]
    # CSV 가 비어있는 합성 — 임시 파일
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".csv", encoding="utf-8", delete=False) as f:
        f.write("거래일자,가맹점명,이용금액\n")
        empty = f.name
    out = await reconcile_csv(
        FakeClient(entries),
        csv_path=empty,
        section_id="s_FAKE",
        start_date="20260501",
        end_date="20260510",
    )
    assert out["summary"]["csv_total"] == 0
    # CSV 가 비어 빠른 종료 — extra 도 0 (date_range 못 정해서)
    # 또는 start/end 명시했으면 entries 도 fetch 됨
    # 본 테스트는 명시했으므로 entries 1개를 detect
    assert out["summary"]["whooing_total"] == 1
    assert out["summary"]["extra_in_whooing_count"] == 1


async def test_reconcile_tolerance_days_matches_offset_date():
    entries = [
        {"entry_id": "w1", "entry_date": "20260510", "money": 6200, "item": "스타벅스"},
    ]
    out = await reconcile_csv(
        FakeClient(entries),
        csv_path=str(FIXTURES / "shinhan_sample.csv"),
        section_id="s_FAKE",
        tolerance_days=2,
    )
    # 첫 csv 행 (20260509) 은 후잉 (20260510) 과 1일차로 매칭
    assert out["summary"]["matched_count"] == 1


async def test_reconcile_invalid_path():
    with pytest.raises(ToolError):
        await reconcile_csv(
            FakeClient([]),
            csv_path="relative.csv",
            section_id="s_FAKE",
        )


async def test_reconcile_invalid_issuer():
    with pytest.raises(ToolError):
        await reconcile_csv(
            FakeClient([]),
            csv_path=str(FIXTURES / "shinhan_sample.csv"),
            section_id="s_FAKE",
            issuer="hyundai",
        )
