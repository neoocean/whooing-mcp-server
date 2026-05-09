"""dates.py — KST 정규화 + YYYYMMDD 검증."""

import re

import pytest

from whooing_mcp.dates import (
    KST,
    date_diff_days,
    days_ago_yyyymmdd,
    now_kst,
    parse_yyyymmdd,
    split_yearly_ranges,
    today_yyyymmdd,
)


def test_now_kst_is_kst() -> None:
    assert now_kst().tzinfo is KST


def test_today_format() -> None:
    s = today_yyyymmdd()
    assert re.fullmatch(r"\d{8}", s)


def test_days_ago_negative_raises() -> None:
    with pytest.raises(ValueError):
        days_ago_yyyymmdd(-1)


def test_days_ago_zero_is_today() -> None:
    assert days_ago_yyyymmdd(0) == today_yyyymmdd()


def test_parse_valid() -> None:
    assert parse_yyyymmdd("20260509") == "20260509"


def test_parse_rejects_wrong_length() -> None:
    with pytest.raises(ValueError):
        parse_yyyymmdd("2026509")


def test_parse_rejects_non_digit() -> None:
    with pytest.raises(ValueError):
        parse_yyyymmdd("2026-05-09")


def test_parse_rejects_invalid_calendar_date() -> None:
    with pytest.raises(ValueError):
        parse_yyyymmdd("20260230")  # Feb 30 안 됨


def test_date_diff_days_same_day_zero() -> None:
    assert date_diff_days("20260509", "20260509") == 0


def test_date_diff_days_one_day() -> None:
    assert date_diff_days("20260509", "20260510") == 1
    assert date_diff_days("20260510", "20260509") == 1  # absolute


def test_date_diff_days_across_year() -> None:
    assert date_diff_days("20251231", "20260101") == 1


def test_split_yearly_ranges_single() -> None:
    out = split_yearly_ranges("20260101", "20260601")
    assert out == [("20260101", "20260601")]


def test_split_yearly_ranges_at_boundary() -> None:
    """365일 정확히는 분할 안 됨 (<=365 단일)."""
    out = split_yearly_ranges("20250101", "20260101")  # 365 days
    assert len(out) == 1


def test_split_yearly_ranges_two_years() -> None:
    out = split_yearly_ranges("20240101", "20260101")
    assert len(out) >= 2
    # 첫 청크 시작이 입력 시작
    assert out[0][0] == "20240101"
    # 마지막 청크 끝이 입력 끝
    assert out[-1][1] == "20260101"
    # 청크 사이 빈 날짜 없음 (연속)
    for (s1, e1), (s2, _) in zip(out, out[1:]):
        from datetime import datetime, timedelta
        d_e1 = datetime.strptime(e1, "%Y%m%d")
        d_s2 = datetime.strptime(s2, "%Y%m%d")
        assert (d_s2 - d_e1) == timedelta(days=1)
