"""PDF adapter detection + parsing + reconcile_pdf 회귀."""

from __future__ import annotations

from pathlib import Path

import pytest

from whooing_mcp.models import ToolError
from whooing_mcp.pdf_adapters import detect, known_issuers, parse
from whooing_mcp.tools.reconcile import pdf_format_detect, reconcile_pdf

FIXTURES = Path(__file__).parent / "fixtures" / "pdf"


# ---- detect ---------------------------------------------------------------


def test_known_issuers():
    assert "shinhan_card" in known_issuers()
    assert "hyundai_card" in known_issuers()


def test_detect_shinhan():
    d = detect(str(FIXTURES / "shinhan_sample.pdf"))
    assert d.detected_issuer == "shinhan_card"
    assert d.confidence > 0.5
    assert "신한카드" in d.first_page_excerpt


def test_detect_hyundai():
    d = detect(str(FIXTURES / "hyundai_sample.pdf"))
    assert d.detected_issuer == "hyundai_card"
    assert d.confidence > 0.5


# ---- parse ----------------------------------------------------------------


def test_parse_shinhan_rows():
    issuer, rows = parse(str(FIXTURES / "shinhan_sample.pdf"))
    assert issuer == "shinhan_card"
    assert len(rows) == 3
    r = rows[0]
    assert r.date == "20260509"
    assert r.amount == 6200
    assert "스타벅스" in r.merchant


def test_parse_hyundai_rows():
    issuer, rows = parse(str(FIXTURES / "hyundai_sample.pdf"))
    assert issuer == "hyundai_card"
    assert len(rows) == 2


def test_parse_explicit_issuer():
    issuer, _ = parse(str(FIXTURES / "shinhan_sample.pdf"), issuer="shinhan_card")
    assert issuer == "shinhan_card"


def test_parse_unknown_issuer_raises():
    with pytest.raises(ValueError):
        parse(str(FIXTURES / "shinhan_sample.pdf"), issuer="lotte_card")


# ---- pdf_format_detect (tool) -------------------------------------------


async def test_format_detect_envelope():
    out = await pdf_format_detect(str(FIXTURES / "shinhan_sample.pdf"))
    assert out["detected_issuer"] == "shinhan_card"
    assert "first_page_excerpt" in out
    assert "supported_issuers" in out


async def test_format_detect_relative_rejected():
    with pytest.raises(ToolError):
        await pdf_format_detect("relative/path.pdf")


async def test_format_detect_missing_rejected():
    with pytest.raises(ToolError):
        await pdf_format_detect("/tmp/__nonexistent_whooing__.pdf")


# ---- reconcile_pdf ------------------------------------------------------


class FakeClient:
    def __init__(self, entries):
        self._entries = entries

    async def list_entries(self, *, section_id, start_date, end_date):
        return [
            e for e in self._entries
            if start_date <= (e.get("entry_date") or "") <= end_date
        ]


async def test_reconcile_pdf_all_matched():
    """후잉에 PDF 의 모든 거래가 동일하게 있으면 matched=3."""
    entries = [
        {"entry_id": "w1", "entry_date": "20260509", "money": 6200, "item": "스타벅스 강남"},
        {"entry_id": "w2", "entry_date": "20260508", "money": 3500, "item": "GS25 합정"},
        {"entry_id": "w3", "entry_date": "20260507", "money": 18000, "item": "합성식당"},
    ]
    out = await reconcile_pdf(
        FakeClient(entries),
        pdf_path=str(FIXTURES / "shinhan_sample.pdf"),
        section_id="s_FAKE",
    )
    assert out["adapter_used"] == "shinhan_card"
    assert out["input_type"] == "pdf"
    assert out["summary"]["csv_total"] == 3
    assert out["summary"]["matched_count"] == 3


async def test_reconcile_pdf_missing():
    out = await reconcile_pdf(
        FakeClient([]),
        pdf_path=str(FIXTURES / "shinhan_sample.pdf"),
        section_id="s_FAKE",
    )
    assert out["summary"]["missing_in_whooing_count"] == 3


async def test_reconcile_pdf_invalid_path():
    with pytest.raises(ToolError):
        await reconcile_pdf(
            FakeClient([]),
            pdf_path="relative.pdf",
            section_id="s_FAKE",
        )


async def test_reconcile_pdf_invalid_issuer():
    with pytest.raises(ToolError):
        await reconcile_pdf(
            FakeClient([]),
            pdf_path=str(FIXTURES / "shinhan_sample.pdf"),
            section_id="s_FAKE",
            issuer="lotte_card",
        )
