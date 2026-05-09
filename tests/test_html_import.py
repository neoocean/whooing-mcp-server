"""tools/html_import.py + html_adapters/ — 입력 검증 + 파서 회귀."""

from __future__ import annotations

from pathlib import Path

import pytest

from whooing_mcp.csv_adapters.base import CSVRow
from whooing_mcp.html_adapters.hanacard_secure_mail import (
    _extract_rows_from_decrypted,
    is_match,
)
from whooing_mcp.models import ToolError
from whooing_mcp.tools.html_import import import_html_statement


# ---- detector ---------------------------------------------------------


def test_is_match_true_with_keywords():
    head = "<html><head><title>하나카드 보안메일</title></head><body>uni_func_check_pass... CryptoJS"
    assert is_match(head) is True


def test_is_match_false_for_other_card():
    head = "<html><head><title>다른카드</title></head><body>different format"
    assert is_match(head) is False


# ---- _extract_rows_from_decrypted -------------------------------------


def test_extract_basic_rows():
    """전형적인 거래 5건 — 일반 + 할인 + 외화 (수수료) 혼합."""
    plain_html = """
<html><body>
<table>
<tr><td>03/15</td></tr>
<tr><td>조원관광진흥주식회사</td></tr>
<tr><td>41,200</td></tr>
<tr><td>41,200</td></tr>
<tr><td>03/15</td></tr>

<tr><td>03/15</td></tr>
<tr><td>SKT통신요금할인받으신금액</td></tr>
<tr><td>15,000</td></tr>
<tr><td>할인</td></tr>
<tr><td>-15,000</td></tr>

<tr><td>03/19</td></tr>
<tr><td>PAYPAL ARLOTECHNOL</td></tr>
<tr><td>30,476</td></tr>
<tr><td>30,476</td></tr>
<tr><td>60</td></tr>
</table>
</body></html>
"""
    rows = _extract_rows_from_decrypted(plain_html)
    # rough check — 3 unique txns
    assert len(rows) == 3

    # row 1: 조원 41200 KRW
    조원 = next((r for r in rows if "조원" in r.merchant), None)
    assert 조원 is not None
    assert 조원.amount == 41200

    # row 2: SKT 할인 -15000
    skt = next((r for r in rows if "SKT" in r.merchant), None)
    assert skt is not None
    assert skt.amount == -15000

    # row 3: PAYPAL 30476 + 60 fee = 30536
    paypal = next((r for r in rows if "PAYPAL" in r.merchant), None)
    assert paypal is not None
    assert paypal.amount == 30536  # principal + fee
    assert paypal.raw["fee"] == 60


def test_extract_dedups_within_html():
    """HTML 의 같은 거래가 여러 섹션에 나타나도 unique 화."""
    plain_html = """
<table>
<tr><td>03/15</td></tr>
<tr><td>같은가게</td></tr>
<tr><td>10,000</td></tr>
<tr><td>10,000</td></tr>
<tr><td>03/15</td></tr>

<tr><td>03/15</td></tr>
<tr><td>같은가게</td></tr>
<tr><td>10,000</td></tr>
<tr><td>10,000</td></tr>
<tr><td>03/15</td></tr>
</table>
"""
    rows = _extract_rows_from_decrypted(plain_html)
    assert len(rows) == 1


def test_extract_skips_non_matching_lines():
    """date 패턴 아닌 line 은 무시."""
    plain_html = """
<p>이용내역</p>
<p>일반 텍스트</p>
<table>
<tr><td>03/15</td></tr>
<tr><td>가게A</td></tr>
<tr><td>5,000</td></tr>
<tr><td>5,000</td></tr>
<tr><td>03/15</td></tr>
</table>
<p>합계</p>
"""
    rows = _extract_rows_from_decrypted(plain_html)
    assert len(rows) == 1


# ---- import_html_statement 입력 검증 ---------------------------------


class FakeClient:
    async def list_entries(self, *, section_id, start_date, end_date):
        return []
    async def list_accounts(self, section_id):
        return {
            "expenses": [{"account_id": "x50", "title": "식비"}],
            "liabilities": [{"account_id": "x80", "title": "[우진]하나카드"}],
            "assets": [], "income": [], "capital": [],
        }


@pytest.fixture
def env_with_password(monkeypatch, tmp_path):
    monkeypatch.setenv("WHOOING_HANACARD_PASSWORD", "test123456")
    monkeypatch.setenv("WHOOING_AI_TOKEN", "__eyJh" + "x" * 100)
    monkeypatch.setenv("WHOOING_QUEUE_PATH", str(tmp_path / "queue.db"))
    yield


async def test_relative_path_rejected(env_with_password, tmp_path):
    with pytest.raises(ToolError):
        await import_html_statement(
            FakeClient(), html_path="relative.html",
            section_id="s_FAKE", r_account_id="x80"
        )


async def test_missing_file_rejected(env_with_password):
    with pytest.raises(ToolError):
        await import_html_statement(
            FakeClient(), html_path="/tmp/__nonexistent__.html",
            section_id="s_FAKE", r_account_id="x80"
        )


async def test_missing_password_rejected(monkeypatch, tmp_path):
    monkeypatch.delenv("WHOOING_HANACARD_PASSWORD", raising=False)
    monkeypatch.setenv("WHOOING_QUEUE_PATH", str(tmp_path / "q.db"))
    monkeypatch.setenv("WHOOING_AI_TOKEN", "__eyJh" + "x" * 100)
    # use a real existing file for path check to pass
    existing = tmp_path / "fake.html"
    existing.write_text("<html></html>")
    with pytest.raises(ToolError) as ex:
        await import_html_statement(
            FakeClient(), html_path=str(existing),
            section_id="s_FAKE", r_account_id="x80",
        )
    assert "WHOOING_HANACARD_PASSWORD" in ex.value.message


async def test_missing_r_account_id_rejected(env_with_password, tmp_path):
    existing = tmp_path / "fake.html"
    existing.write_text("<html></html>")
    with pytest.raises(ToolError) as ex:
        await import_html_statement(
            FakeClient(), html_path=str(existing),
            section_id="s_FAKE", r_account_id=""
        )
    assert "r_account_id" in ex.value.message


async def test_insert_without_confirm_rejected(env_with_password, tmp_path):
    existing = tmp_path / "fake.html"
    existing.write_text("<html></html>")
    with pytest.raises(ToolError) as ex:
        await import_html_statement(
            FakeClient(), html_path=str(existing),
            section_id="s_FAKE", r_account_id="x80",
            dry_run=False, confirm_insert=False,
        )
    assert "confirm_insert" in ex.value.message
