"""SMS 파서 회귀 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from whooing_mcp.dates import now_kst
from whooing_mcp.models import ToolError
from whooing_mcp.parsers import sms as sms_parsers
from whooing_mcp.tools.sms import parse_payment_sms

FIXTURES = Path(__file__).parent / "fixtures" / "sms"
YEAR = now_kst().year


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---- shinhan_card --------------------------------------------------------


def test_shinhan_web_multiline():
    r = sms_parsers.parse(_read("shinhan_web_multiline.txt"))
    assert r is not None
    assert r.parser_used == "shinhan_card.v1"
    assert r.proposed_entry["money"] == 6200
    assert r.proposed_entry["entry_date"] == f"{YEAR}0509"
    assert "스타벅스" in r.proposed_entry["merchant"]
    assert r.proposed_entry["suggested_r_account"] == "신한카드"
    assert r.proposed_entry["direction"] == "expense"
    assert r.confidence >= 0.85


def test_shinhan_push_oneline():
    r = sms_parsers.parse(_read("shinhan_push_oneline.txt"))
    assert r is not None
    assert r.proposed_entry["money"] == 12500
    assert "GS25" in r.proposed_entry["merchant"]


def test_shinhan_installment_marked_in_notes():
    r = sms_parsers.parse(_read("shinhan_installment.txt"))
    assert r is not None
    assert r.proposed_entry["money"] == 350000
    assert any("할부" in n and "X" not in n for n in r.notes)


# ---- kookmin_card --------------------------------------------------------


def test_kookmin_standard():
    r = sms_parsers.parse(_read("kookmin_standard.txt"))
    assert r is not None
    assert r.parser_used == "kookmin_card.v1"
    assert r.proposed_entry["money"] == 6200
    assert r.proposed_entry["suggested_r_account"] == "국민카드"
    # 누적 1,234,567원 잡음이 merchant 에 새지 않아야 함
    assert "1,234,567" not in r.proposed_entry["merchant"]


def test_kookmin_push_oneline():
    r = sms_parsers.parse(_read("kookmin_push_oneline.txt"))
    assert r is not None
    assert r.proposed_entry["money"] == 25000
    assert "합성마트" in r.proposed_entry["merchant"]


# ---- 음성 (negative) ----------------------------------------------------


def test_unsupported_returns_none():
    assert sms_parsers.parse(_read("unsupported_random.txt")) is None


def test_explicit_hint_only_uses_named_parser():
    """힌트가 있으면 그 파서만 시도 — 다른 issuer 의 텍스트는 None."""
    text = _read("kookmin_standard.txt")
    assert sms_parsers.parse(text, issuer_hint="shinhan_card") is None
    r = sms_parsers.parse(text, issuer_hint="kookmin_card")
    assert r is not None


# ---- whooing_parse_payment_sms (tool wrapper) ---------------------------


async def test_tool_returns_envelope_on_match():
    out = await parse_payment_sms(_read("shinhan_web_multiline.txt"))
    assert out["proposed_entry"] is not None
    assert "next_step_hint" in out
    assert out["confidence"] >= 0.85


async def test_tool_returns_no_match_envelope():
    out = await parse_payment_sms(_read("unsupported_random.txt"))
    assert out["proposed_entry"] is None
    assert out["confidence"] == 0.0
    assert out["parser_used"] is None
    assert "supported_issuers" in out


async def test_tool_rejects_empty_text():
    with pytest.raises(ToolError):
        await parse_payment_sms("")


async def test_tool_rejects_unknown_hint():
    with pytest.raises(ToolError) as ex:
        await parse_payment_sms("anything", issuer_hint="lotte_card")  # 미지원 issuer
    assert ex.value.kind == "USER_INPUT"
    assert "lotte_card" in str(ex.value)


def test_known_issuers_listed():
    issuers = sms_parsers.known_issuers()
    for expected in (
        "shinhan_card", "kookmin_card",
        "hyundai_card", "samsung_card",
        "kakaobank", "toss", "woori_bank",
    ):
        assert expected in issuers, f"missing issuer: {expected}"


# ---- 신규 issuer 회귀 (CL #11) ------------------------------------------


def test_hyundai_web_multiline():
    r = sms_parsers.parse(_read("hyundai_web_multiline.txt"))
    assert r is not None
    assert r.parser_used == "hyundai_card.v1"
    assert r.proposed_entry["money"] == 6200
    assert r.proposed_entry["suggested_r_account"] == "현대카드"
    assert r.proposed_entry["entry_date"] == f"{YEAR}0509"


def test_hyundai_installment_marked():
    r = sms_parsers.parse(_read("hyundai_installment.txt"))
    assert r is not None
    assert r.proposed_entry["money"] == 240000
    assert any("할부" in n and "X" not in n for n in r.notes)


def test_samsung_standard_strips_누적():
    r = sms_parsers.parse(_read("samsung_standard.txt"))
    assert r is not None
    assert r.parser_used == "samsung_card.v1"
    assert r.proposed_entry["money"] == 6200
    assert "1,234,567" not in r.proposed_entry["merchant"]


def test_samsung_push_oneline():
    r = sms_parsers.parse(_read("samsung_push_oneline.txt"))
    assert r is not None
    assert r.proposed_entry["money"] == 35000
    assert "합성식당" in r.proposed_entry["merchant"]


def test_toss_oneline():
    r = sms_parsers.parse(_read("toss_oneline.txt"))
    assert r is not None
    assert r.parser_used == "toss.v1"
    assert r.proposed_entry["money"] == 6200
    assert r.proposed_entry["suggested_r_account"] == "토스"


def test_tosscard_approve():
    r = sms_parsers.parse(_read("tosscard_approve.txt"))
    assert r is not None
    assert r.parser_used == "toss.v1"
    assert r.proposed_entry["money"] == 12500


def test_kakaobank_standard():
    r = sms_parsers.parse(_read("kakaobank_standard.txt"))
    assert r is not None
    assert r.parser_used == "kakaobank.v1"
    assert r.proposed_entry["money"] == 6200
    assert r.proposed_entry["suggested_r_account"] == "카카오뱅크"
    # 잔액 잡음 제거
    assert "1,234,567" not in r.proposed_entry["merchant"]


def test_woori_check_card():
    r = sms_parsers.parse(_read("woori_check.txt"))
    assert r is not None
    assert r.parser_used == "woori_bank.v1"
    assert r.proposed_entry["money"] == 6200
    assert r.proposed_entry["suggested_r_account"] == "우리은행"


def test_explicit_hint_for_new_issuers():
    """힌트 명시 시 다른 issuer 텍스트는 None."""
    text = _read("hyundai_web_multiline.txt")
    assert sms_parsers.parse(text, issuer_hint="samsung_card") is None
    assert sms_parsers.parse(text, issuer_hint="hyundai_card") is not None
