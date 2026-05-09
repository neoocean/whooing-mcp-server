"""dates.py — KST 정규화 + YYYYMMDD 검증."""

import re

import pytest

from whooing_mcp.dates import (
    KST,
    days_ago_yyyymmdd,
    now_kst,
    parse_yyyymmdd,
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
