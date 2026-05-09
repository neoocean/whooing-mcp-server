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
        await parse_payment_sms("anything", issuer_hint="hyundai_card")
    assert ex.value.kind == "USER_INPUT"
    assert "hyundai_card" in str(ex.value)


def test_known_issuers_listed():
    assert "shinhan_card" in sms_parsers.known_issuers()
    assert "kookmin_card" in sms_parsers.known_issuers()
