"""errors.py — HTTP 매핑 + sanitize 회귀."""

from __future__ import annotations

import pytest

from whooing_mcp.errors import (
    SECRET_KEYS,
    map_response,
    sanitize_for_log,
    sanitize_token,
)
from whooing_mcp.models import ToolError


# ---- map_response --------------------------------------------------------


def test_400_user_input():
    e = map_response(400, "missing param", {"error_parameters": {"section_id": "required"}})
    assert e.kind == "USER_INPUT"
    assert "missing param" in e.message
    assert e.details["error_parameters"] == {"section_id": "required"}


def test_401_auth():
    e = map_response(401, "expired")
    assert e.kind == "AUTH"
    assert "재발급" in e.message


def test_405_auth_same_as_401():
    e = map_response(405, "revoked")
    assert e.kind == "AUTH"


def test_402_rate_limit_daily():
    e = map_response(402, "", {"rest_of_api": 0})
    assert e.kind == "RATE_LIMIT"
    assert "일일" in e.message
    assert e.details["rest_of_api"] == 0


def test_429_rate_limit_minute():
    e = map_response(429, "")
    assert e.kind == "RATE_LIMIT"
    assert "분당" in e.message


def test_500_upstream():
    e = map_response(500, "internal")
    assert e.kind == "UPSTREAM"


def test_unknown_code_upstream():
    e = map_response(418, "I'm a teapot")
    assert e.kind == "UPSTREAM"


def test_status_passed_through():
    e = map_response(401, "x", status=401)
    assert e.details["http_status"] == 401


# ---- sanitize_for_log ---------------------------------------------------


def test_sanitize_masks_webhook_token():
    raw = {"section_id": "s9046", "webhook_token": "1234-5678-9999"}
    out = sanitize_for_log(raw)
    assert out["section_id"] == "s9046"
    assert out["webhook_token"] == "***masked***"


def test_sanitize_masks_known_secret_keys():
    raw = {"token": "abc", "password": "x", "api_key": "y", "secret": "z", "signature": "s"}
    out = sanitize_for_log(raw)
    for k in raw:
        assert out[k] == "***masked***"


def test_sanitize_does_not_mutate_input():
    raw = {"webhook_token": "secret123"}
    out = sanitize_for_log(raw)
    assert raw["webhook_token"] == "secret123"  # 원본 유지
    assert out["webhook_token"] == "***masked***"


def test_sanitize_recursive_dict():
    raw = {"section": {"webhook_token": "secret"}}
    out = sanitize_for_log(raw)
    assert out["section"]["webhook_token"] == "***masked***"


def test_sanitize_recursive_list():
    raw = [{"webhook_token": "a"}, {"webhook_token": "b"}]
    out = sanitize_for_log(raw)
    assert all(item["webhook_token"] == "***masked***" for item in out)


def test_sanitize_passthrough_non_secret():
    raw = {"a": 1, "b": "hello"}
    assert sanitize_for_log(raw) == raw


def test_sanitize_case_insensitive():
    raw = {"Webhook_Token": "x", "WEBHOOK_TOKEN": "y"}
    out = sanitize_for_log(raw)
    assert out["Webhook_Token"] == "***masked***"
    assert out["WEBHOOK_TOKEN"] == "***masked***"


# ---- sanitize_token -----------------------------------------------------


def test_sanitize_token_normal():
    out = sanitize_token("__eyJhSECRETSECRETxyzw1234")
    assert "SECRET" not in out
    assert "1234" in out  # 마지막 4자리 hint
    assert "len=" in out


def test_sanitize_token_short():
    """짧은 토큰은 마지막 4자리 hint 도 안 노출."""
    raw = "abc12345"  # 8자, "short" 단어 안 포함
    out = sanitize_token(raw)
    assert raw not in out
    assert out == "***short***"


def test_sanitize_token_empty():
    out = sanitize_token("")
    assert "***empty***" in out


# ---- SECRET_KEYS sanity -------------------------------------------------


def test_secret_keys_includes_webhook_token():
    assert "webhook_token" in SECRET_KEYS
