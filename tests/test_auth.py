"""auth.py — 토큰 마스크 회귀 (DESIGN §13)."""

from whooing_mcp.auth import WhooingAuth


def test_repr_masks_token() -> None:
    auth = WhooingAuth(token="__eyJhSECRETSECRETSECRETabcd")
    s = repr(auth)
    assert "SECRET" not in s
    assert "eyJh" not in s
    assert s.endswith("len=28)")
    assert "***abcd" in s  # 마지막 4자리 hint 만 노출


def test_str_also_masks() -> None:
    auth = WhooingAuth(token="__eyJhDONOTLEAKxyz9")
    assert "DONOTLEAK" not in str(auth)


def test_short_token_no_hint() -> None:
    """길이 12 이하의 토큰은 hint 도 안 남김 (오용 방지)."""
    auth = WhooingAuth(token="short")
    assert repr(auth) == "WhooingAuth(token=***)"


def test_headers_uses_x_api_key() -> None:
    auth = WhooingAuth(token="__eyJhTESTTESTTEST")
    h = auth.headers()
    assert h == {"X-API-Key": "__eyJhTESTTESTTEST"}
