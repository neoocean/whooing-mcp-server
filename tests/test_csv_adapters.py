"""CSV adapter detection + parsing tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from whooing_mcp.csv_adapters import detect, known_issuers, parse
from whooing_mcp.csv_adapters.base import parse_date, parse_money

FIXTURES = Path(__file__).parent / "fixtures" / "csv"


# ---- helpers --------------------------------------------------------------


def test_parse_money_simple():
    assert parse_money("6,200") == 6200
    assert parse_money("6200원") == 6200


def test_parse_money_negative():
    assert parse_money("-6,200") == -6200


def test_parse_money_rejects_empty():
    with pytest.raises(ValueError):
        parse_money("원")


def test_parse_date_dashed():
    assert parse_date("2026-05-09") == "20260509"


def test_parse_date_slashed():
    assert parse_date("2026/05/09") == "20260509"


def test_parse_date_yymmdd_assumes_2000s():
    assert parse_date("260509") == "20260509"


def test_parse_date_rejects_unrecognized():
    with pytest.raises(ValueError):
        parse_date("not-a-date")


# ---- detect ---------------------------------------------------------------


def test_detect_shinhan():
    d = detect(str(FIXTURES / "shinhan_sample.csv"))
    assert d.detected_issuer == "shinhan_card"
    assert d.confidence > 0.5
    assert "거래일자" in d.header_sample


def test_detect_kookmin():
    d = detect(str(FIXTURES / "kookmin_sample.csv"))
    assert d.detected_issuer == "kookmin_card"
    assert d.confidence > 0.5


def test_detect_proposed_mapping_present():
    d = detect(str(FIXTURES / "shinhan_sample.csv"))
    m = d.column_mapping_proposed
    assert m["date_col"] == "거래일자"
    assert m["amount_col"] == "이용금액"
    assert m["merchant_col"] == "가맹점명"


# ---- parse ----------------------------------------------------------------


def test_parse_shinhan_rows():
    issuer, rows = parse(str(FIXTURES / "shinhan_sample.csv"))
    assert issuer == "shinhan_card"
    assert len(rows) == 5
    r = rows[0]
    assert r.date == "20260509"
    assert r.amount == 6200
    assert "스타벅스" in r.merchant


def test_parse_kookmin_rows():
    issuer, rows = parse(str(FIXTURES / "kookmin_sample.csv"))
    assert issuer == "kookmin_card"
    assert len(rows) == 3
    assert rows[0].date == "20260509"
    assert rows[0].amount == 6200


def test_parse_with_explicit_issuer():
    issuer, rows = parse(str(FIXTURES / "shinhan_sample.csv"), issuer="shinhan_card")
    assert issuer == "shinhan_card"
    assert len(rows) == 5


def test_parse_unknown_issuer_raises():
    with pytest.raises(ValueError):
        parse(str(FIXTURES / "shinhan_sample.csv"), issuer="hyundai_card")


def test_known_issuers():
    issuers = known_issuers()
    assert "shinhan_card" in issuers
    assert "kookmin_card" in issuers
