"""p4_sync.py — subprocess mocking 으로 동작 검증.

실 P4 환경 없이도 동작 검증. 모든 외부 호출은 monkeypatch.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from whooing_mcp import p4_sync


@pytest.fixture(autouse=True)
def reset_p4_cache(monkeypatch, tmp_path):
    """매 테스트마다 _P4_AVAILABLE + config 캐시 초기화 + p4_sync 강제 enabled.

    config disabled 가 default 라 다른 모든 테스트는 always-skipped 가
    되므로, 명시적으로 p4_sync 강제 활성화. config disabled 케이스는
    별도 테스트.
    """
    from whooing_mcp import config as config_mod
    monkeypatch.setattr(p4_sync, "_P4_AVAILABLE", None)
    config_mod.reset_cache()
    cfg_file = tmp_path / "test-config.toml"
    cfg_file.write_text('[p4_sync]\nenabled = true\n')
    monkeypatch.setenv("WHOOING_CONFIG", str(cfg_file))
    yield
    config_mod.reset_cache()


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


def test_skip_when_config_disabled(tmp_db_in_path, monkeypatch, tmp_path):
    """config 의 p4_sync.enabled=false 면 즉시 silent skip — db / p4 검사 X."""
    from whooing_mcp import config as config_mod
    config_mod.reset_cache()
    cfg_file = tmp_path / "off.toml"
    cfg_file.write_text('[p4_sync]\nenabled = false\n')
    monkeypatch.setenv("WHOOING_CONFIG", str(cfg_file))
    out = p4_sync.sync_db_to_p4("test")
    assert out["ok"] is True and out["skipped"] is True
    assert "비활성화" in out["message"]


def test_skip_when_p4_unavailable(tmp_db_in_path, monkeypatch):
    monkeypatch.setattr(p4_sync, "is_p4_available", lambda: False)
    out = p4_sync.sync_db_to_p4("test")
    assert out["skipped"] is True


def test_db_not_in_depot_now_added(tmp_db_in_path, monkeypatch):
    """v0.1.10 변경: depot 미등록이면 skip 대신 자동 add (sync_paths_to_p4)."""
    monkeypatch.setattr(p4_sync, "is_p4_available", lambda: True)
    monkeypatch.setattr(p4_sync, "is_db_in_depot", lambda _: False)
    # _detect_p4_action 내부에서 reconcile -n -a 호출 → "opened for add" 가짜 응답
    _mock_subprocess(monkeypatch, [
        (0, "//.../db.sqlite - opened for add", ""),  # reconcile -n -a
        (0, "Change 100 created.", ""),                # p4 change -i
        (0, "opened for add", ""),                     # p4 add -c -t binary
        (0, "Change 100 submitted.", ""),              # p4 submit -c
    ])
    out = p4_sync.sync_db_to_p4("first sync")
    assert out["ok"] is True
    assert out["skipped"] is False
    assert out["cl"] == 100
    assert out["files"][0]["action"] == "add"


def test_skip_when_no_changes(tmp_db_in_path, monkeypatch):
    monkeypatch.setattr(p4_sync, "is_p4_available", lambda: True)
    monkeypatch.setattr(p4_sync, "is_db_in_depot", lambda _: True)
    # p4 reconcile -n → 빈 출력 (또는 'no file(s) to reconcile') = no changes
    _mock_subprocess(monkeypatch, [(0, "", "")])
    out = p4_sync.sync_db_to_p4("test")
    assert out["skipped"] is True
    assert "변경 없음" in out["message"]


def test_full_sync_flow(tmp_db_in_path, monkeypatch):
    """reconcile -n (변경 감지) → change -i → edit -c → submit -c 모두 성공."""
    monkeypatch.setattr(p4_sync, "is_p4_available", lambda: True)
    monkeypatch.setattr(p4_sync, "is_db_in_depot", lambda _: True)
    _mock_subprocess(monkeypatch, [
        (0, "//.../whooing-data.sqlite#1 - opened for edit", ""),  # reconcile -n
        (0, "Change 99999 created.", ""),                           # p4 change -i
        (0, "opened for edit", ""),                                 # p4 edit -c
        (0, "Change 99999 submitted.", ""),                         # p4 submit -c
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
        (0, "//.../foo.sqlite - opened for edit", ""),
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
        (0, "//.../db.sqlite - opened for edit", ""),
        (1, "", "Permission denied"),  # p4 change -i fails
    ])
    out = p4_sync.sync_db_to_p4("test")
    assert out["ok"] is False
    assert "Permission denied" in out["message"]


def test_reconcile_to_edit_pattern_also_recognized(tmp_db_in_path, monkeypatch):
    """일부 P4 버전은 'reconcile to edit' 형식으로 출력 — 같이 매칭."""
    monkeypatch.setattr(p4_sync, "is_p4_available", lambda: True)
    monkeypatch.setattr(p4_sync, "is_db_in_depot", lambda _: True)
    _mock_subprocess(monkeypatch, [
        (0, "//foo - reconcile to edit", ""),
        (0, "Change 200 created.", ""),
        (0, "ok", ""),
        (0, "Change 200 submitted.", ""),
    ])
    out = p4_sync.sync_db_to_p4("variant test")
    assert out["ok"] is True
    assert out["cl"] == 200


# ---- _build_description -----------------------------------------------


def test_description_includes_action_and_files(tmp_db_in_path):
    """v0.1.10 변경: _build_description signature: (action, files_to_open list)."""
    from pathlib import Path
    files = [(Path("/tmp/db.sqlite"), "edit"), (Path("/tmp/foo.pdf"), "add")]
    desc = p4_sync._build_description("annotation.set (entry=e1, tags=[식비])", files)
    assert "annotation.set" in desc
    assert "db.sqlite" in desc
    assert "foo.pdf" in desc
    assert "edit" in desc
    assert "add" in desc
    assert "GitHub" in desc
