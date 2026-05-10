"""hyundaicard_secure_mail adapter — column-based row extractor 단위 테스트.

복호화 단계 (Playwright) 는 별도 (실 HTML + .env 필요) — 본 테스트는 이미
복호화된 HTML 의 거래 테이블 파서만 검증.
"""

from __future__ import annotations

from datetime import datetime

from whooing_mcp.html_adapters import detect, known_issuers
from whooing_mcp.html_adapters.hyundaicard_secure_mail import (
    ISSUER,
    _extract_rows_from_decrypted,
    _identify_columns,
    _normalize_date,
    _parse_money,
    is_match,
)


# ---- detect / is_match ------------------------------------------------


def test_known_issuers_contains_hyundai():
    assert "hyundaicard_secure_mail" in known_issuers()


def test_is_match_positive_vestmail_payload():
    head = (
        "<html><script>var b_p='abc';eval(atob(b_p));</script>"
        "<a href='https://www.hyundaicard.com/'>HyundaiCard</a>"
        "<form onsubmit='doAction()'></form></html>"
    )
    assert is_match(head) is True


def test_is_match_negative_hanacard_payload():
    """hanacard 키워드 (CryptoJS) 가 있으면 hyundai detect 실패."""
    head = (
        "<html>하나카드 보안 uni_func() CryptoJS hyundaicard.com</html>"
    )
    assert is_match(head) is False


def test_is_match_negative_unrelated_html():
    assert is_match("<html><body>random</body></html>") is False


def test_detect_with_real_file_excerpt(tmp_path):
    """실 현대카드 HTML 의 head excerpt (1MB 이내) 가 hyundai 로 detect 되는지."""
    fake = tmp_path / "x.html"
    body = (
        "<html><head><title>현대카드 Email</title>"
        "<script src='https://www.hyundaicard.com/em/...'></script>"
        + "X" * 30000  # padding to push vestmail past 8KB
        + "<script>vestmail_msg='x';</script>"
        + "X" * 30000
        + "<script>var b_p='YWJj';eval(atob(b_p));function doAction(){}</script>"
        + "<a>HyundaiCard footer</a>"
        + "</head></html>"
    )
    fake.write_text(body, encoding="utf-8")
    d = detect(str(fake))
    assert d.detected_issuer == "hyundaicard_secure_mail"
    assert d.confidence > 0


# ---- helpers ---------------------------------------------------------


def test_normalize_date_full_yyyy_mm_dd():
    assert _normalize_date("2026/04/13", 2026) == "20260413"
    assert _normalize_date("2026-04-13", 2026) == "20260413"
    assert _normalize_date("2026.04.13", 2026) == "20260413"


def test_normalize_date_md_uses_current_year():
    assert _normalize_date("04/13", 2026) == "20260413"


def test_normalize_date_invalid():
    assert _normalize_date("not-a-date", 2026) is None
    assert _normalize_date("", 2026) is None
    assert _normalize_date("13/40", 2026) is None  # invalid month/day


def test_parse_money_with_commas():
    assert _parse_money("70,436") == 70436
    assert _parse_money("-15,000") == -15000
    assert _parse_money("300") == 300
    assert _parse_money("") == 0


# ---- _identify_columns + _extract_rows_from_decrypted -----------------


_FIXTURE_TABLE = """
<table>
  <tr>
    <th>이용일</th>
    <th>이용카드</th>
    <th>이용가맹점</th>
    <th>이용금액</th>
    <th>할부 /회차</th>
    <th>적립/ 할인율(%)</th>
    <th>예상적립/ 할인</th>
    <th>결제 원금</th>
    <th>결제 후 잔액</th>
    <th>수수료 (이자)</th>
  </tr>
  <tr>
    <td>04/13</td><td>본인 ZERO MOBILE 할인형</td><td>카드이용알림수수료04월</td>
    <td>0</td><td>0/0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>300</td>
  </tr>
  <tr>
    <td>03/20</td><td>본인 ZERO MOBILE 할인형</td><td>파리바게뜨카페서초역점</td>
    <td>2,900</td><td>0/0</td><td>0.7</td><td>-21</td><td>2,879</td><td>0</td><td>0</td>
  </tr>
  <tr>
    <td>04/05</td><td>본인 ZERO MOBILE 할인형</td><td>HetznerOnline,USD:46.00</td>
    <td>70,932</td><td>0/0</td><td>0.7</td><td>-496</td><td>70,436</td><td>0</td><td>0</td>
  </tr>
  <tr>
    <td></td><td>일시불소계3건</td><td></td>
    <td>0</td><td></td><td></td><td>0</td><td>73,315</td><td>0</td><td>300</td>
  </tr>
</table>
"""


def test_identify_columns_picks_hyundai_layout():
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(_FIXTURE_TABLE, "html.parser")
    table = soup.find("table")
    cols = _identify_columns(table)
    assert cols == {"date": 0, "merchant": 2, "amount": 7, "fee": 9}


def test_identify_columns_returns_none_for_unrelated_table():
    from bs4 import BeautifulSoup
    soup = BeautifulSoup("<table><tr><th>foo</th><th>bar</th></tr></table>", "html.parser")
    table = soup.find("table")
    assert _identify_columns(table) is None


def test_extract_rows_uses_amount_plus_fee():
    """결제원금 + 수수료 합산. 소계행 (이용일 빈) 자동 제외."""
    rows = _extract_rows_from_decrypted(_FIXTURE_TABLE)
    assert len(rows) == 3
    by_merchant = {r.merchant: r for r in rows}

    # fee-only row: amt=0, fee=300 → total 300
    fee_row = by_merchant["카드이용알림수수료04월"]
    assert fee_row.amount == 300
    assert fee_row.raw["amount"] == 0
    assert fee_row.raw["fee"] == 300

    # normal row: amt=2879, fee=0 → 2879
    paris = by_merchant["파리바게뜨카페서초역점"]
    assert paris.amount == 2879

    # foreign tx with KRW conversion: 결제원금 = 70436
    hetz = by_merchant["HetznerOnline,USD:46.00"]
    assert hetz.amount == 70436


def test_extract_rows_sets_current_year():
    rows = _extract_rows_from_decrypted(_FIXTURE_TABLE)
    current_year = datetime.now().year
    for r in rows:
        assert r.date.startswith(f"{current_year:04d}")


def test_extract_rows_dedup_identical_entries():
    """같은 (date, amount, merchant prefix) 는 1건으로 dedup."""
    dup_table = _FIXTURE_TABLE.replace(
        "<td>03/20</td>",
        # duplicate the bakery row by inserting a copy below the original
        "<td>03/20</td>", 1,
    )
    # Add literal duplicate
    dup_block = """
      <tr>
        <td>03/20</td><td>본인 ZERO MOBILE 할인형</td><td>파리바게뜨카페서초역점</td>
        <td>2,900</td><td>0/0</td><td>0.7</td><td>-21</td><td>2,879</td><td>0</td><td>0</td>
      </tr>
    """
    dup_table = dup_table.replace("</table>", dup_block + "</table>")
    rows = _extract_rows_from_decrypted(dup_table)
    # 3 unique rows (duplicate dropped)
    assert len(rows) == 3


def test_extract_rows_empty_html_returns_empty():
    assert _extract_rows_from_decrypted("<html></html>") == []


def test_issuer_constant():
    assert ISSUER == "hyundaicard_secure_mail"
