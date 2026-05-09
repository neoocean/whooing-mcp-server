"""p4_sync.py — subprocess mocking 으로 동작 검증.

실 P4 환경 없이도 동작 검증. 모든 외부 호출은 monkeypatch.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from whooing_mcp import p4_sync


@pytest.fixture(autouse=True)
def reset_p4_cache(monkeypatch):
    """매 테스트마다 _P4_AVAILABLE 캐시 초기화."""
    monkeypatch.setattr(p4_sync, "_P4_AVAILABLE", None)
    yield


@pytest.fixture
def tmp_db_in_path(tmp_path, monkeypatch):
    """임시 db 파일을 만들고 default_queue_path 가 그것을 가리키게."""
    db = tmp_path / "whooing-data.sqlite"
    db.write_text("dummy content")
    monkeypatch.setenv("WHOOING_QUEUE_PATH", str(db))
    return db


def _mock_subprocess(monkeypatch, responses):
    """`subprocess.run` 을 모킹. responses 는 [(returncode, stdout, stderr), ...]."""
    iter_resp = iter(responses)

    def fake_run(*args, **kwargs):
        rc, stdout, stderr = next(iter_resp)
        result = subprocess.CompletedProcess(
            args=args[0] if args else [], returncode=rc, stdout=stdout, stderr=stderr
        )
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)


# ---- is_p4_available ----------------------------------------------------


def test_p4_unavailable_when_not_in_path(monkeypatch):
    monkeypatch.setattr(p4_sync.shutil, "which", lambda x: None)
    assert p4_sync.is_p4_available() is False


def test_p4_available_when_info_succeeds(monkeypatch):
    monkeypatch.setattr(p4_sync.shutil, "which", lambda x: "/usr/local/bin/p4")
    _mock_subprocess(monkeypatch, [(0, "User name: x", "")])
    assert p4_sync.is_p4_available() is True


def test_p4_unavailable_when_info_fails(monkeypatch):
    monkeypatch.setattr(p4_sync.shutil, "which", lambda x: "/usr/local/bin/p4")
    _mock_subprocess(monkeypatch, [(1, "", "Connect refused")])
    assert p4_sync.is_p4_available() is False


# ---- sync_db_to_p4 ------------------------------------------------------


def test_skip_when_db_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("WHOOING_QUEUE_PATH", str(tmp_path / "nonexistent.sqlite"))
    out = p4_sync.sync_db_to_p4("test")
    assert out["ok"] is True and out["skipped"] is True


def test_skip_when_p4_unavailable(tmp_db_in_path, monkeypatch):
    monkeypatch.setattr(p4_sync, "is_p4_available", lambda: False)
    out = p4_sync.sync_db_to_p4("test")
    assert out["skipped"] is True


def test_skip_when_db_not_in_depot(tmp_db_in_path, monkeypatch):
    monkeypatch.setattr(p4_sync, "is_p4_available", lambda: True)
    monkeypatch.setattr(p4_sync, "is_db_in_depot", lambda _: False)
    out = p4_sync.sync_db_to_p4("test")
    assert out["skipped"] is True
    assert "미등록" in out["message"]


def test_skip_when_no_changes(tmp_db_in_path, monkeypatch):
    monkeypatch.setattr(p4_sync, "is_p4_available", lambda: True)
    monkeypatch.setattr(p4_sync, "is_db_in_depot", lambda _: True)
    # p4 diff -ds → 빈 출력 = no changes
    _mock_subprocess(monkeypatch, [(0, "", "")])
    out = p4_sync.sync_db_to_p4("test")
    assert out["skipped"] is True
    assert "변경 없음" in out["message"]


def test_full_sync_flow(tmp_db_in_path, monkeypatch):
    """diff → change -i → edit -c → submit -c 모두 성공."""
    monkeypatch.setattr(p4_sync, "is_p4_available", lambda: True)
    monkeypatch.setattr(p4_sync, "is_db_in_depot", lambda _: True)
    _mock_subprocess(monkeypatch, [
        (0, "diff: 5 chunks 100 lines changed", ""),  # p4 diff -ds
        (0, "Change 99999 created.", ""),              # p4 change -i
        (0, "opened for edit", ""),                    # p4 edit -c
        (0, "Change 99999 submitted.", ""),            # p4 submit -c
    ])
    out = p4_sync.sync_db_to_p4("test action")
    assert out["ok"] is True
    assert out["skipped"] is False
    assert out["cl"] == 99999


def test_sync_handles_renamed_cl(tmp_db_in_path, monkeypatch):
    """submit 결과 'Change N renamed change M and submitted' 시 M 추출."""
    monkeypatch.setattr(p4_sync, "is_p4_available", lambda: True)
    monkeypatch.setattr(p4_sync, "is_db_in_depot", lambda _: True)
    _mock_subprocess(monkeypatch, [
        (0, "diff change", ""),
        (0, "Change 100 created.", ""),
        (0, "edited", ""),
        (0, "Change 100 renamed change 105 and submitted.", ""),
    ])
    out = p4_sync.sync_db_to_p4("renamed test")
    assert out["cl"] == 105


def test_sync_returns_failure_on_change_create_error(tmp_db_in_path, monkeypatch):
    monkeypatch.setattr(p4_sync, "is_p4_available", lambda: True)
    monkeypatch.setattr(p4_sync, "is_db_in_depot", lambda _: True)
    _mock_subprocess(monkeypatch, [
        (0, "diff", ""),
        (1, "", "Permission denied"),  # p4 change -i fails
    ])
    out = p4_sync.sync_db_to_p4("test")
    assert out["ok"] is False
    assert "Permission denied" in out["message"]


# ---- _build_description -----------------------------------------------


def test_description_includes_action_and_diff(tmp_db_in_path):
    desc = p4_sync._build_description("annotation.set (entry=e1, tags=[식비])", "diff stuff")
    assert "annotation.set" in desc
    assert "diff stuff" in desc
    assert ".sqlite" in desc
    assert "GitHub 으로는 가지 않음" in desc


def test_description_truncates_long_diff(tmp_db_in_path):
    long_diff = "x" * 1000
    desc = p4_sync._build_description("test", long_diff)
    # diff_summary 는 500 + '...' 로 truncate
    assert "..." in desc
    # 전체 description 길이는 합리적
    assert len(desc) < 2000
