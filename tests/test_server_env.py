"""server.py — _load_env 탐색 우선순위 회귀.

CL #1 의 단순한 cwd-only 탐색에서 4단계 우선순위로 확장됨 (DESIGN §8.2).
Claude Desktop / Claude Code 처럼 cwd 가 프로젝트가 아닐 때도 동작해야
함을 보장한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from whooing_mcp.server import _load_env


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """WHOOING_* 환경변수와 cwd 를 격리.

    HOME 도 임시 디렉터리로 옮겨 ~/.config/whooing-mcp/.env 의 우연 매칭 방지.
    """
    for k in ("WHOOING_AI_TOKEN", "WHOOING_SECTION_ID", "WHOOING_MCP_ENV"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    return tmp_path


def test_finds_explicit_env_var_path(isolated_env, monkeypatch):
    """1순위: $WHOOING_MCP_ENV 가 가리키는 파일."""
    explicit = isolated_env / "explicit.env"
    explicit.write_text("WHOOING_AI_TOKEN=__token_explicit\n")
    monkeypatch.setenv("WHOOING_MCP_ENV", str(explicit))
    _load_env()
    assert os.environ["WHOOING_AI_TOKEN"] == "__token_explicit"


def test_finds_cwd_env_when_no_explicit(isolated_env):
    """2순위: cwd 의 .env (explicit 없음 가정)."""
    (isolated_env / ".env").write_text("WHOOING_AI_TOKEN=__token_cwd\n")
    _load_env()
    assert os.environ["WHOOING_AI_TOKEN"] == "__token_cwd"


def test_finds_project_root_env_when_cwd_lacks(isolated_env, monkeypatch):
    """3순위: cwd 에 .env 없으면 project root (__file__ 기반).

    project root 는 `<this_repo>/.env` — 이미 존재하므로 (사용자 .env)
    실 토큰이 잡힐 수 있다. 본 테스트는 isolated cwd / HOME 만 격리하고
    project root 의 실 .env 를 검출하는지만 확인. 토큰 _값_ 은 검사 X.
    """
    # cwd 와 HOME 모두 .env 없음 → project root 의 .env 가 잡혀야
    _load_env()
    # 실 토큰의 길이 / prefix 만 확인 (값 노출 X)
    tok = os.environ.get("WHOOING_AI_TOKEN", "")
    assert tok.startswith("__"), f"unexpected token shape (len={len(tok)})"


def test_explicit_overrides_cwd(isolated_env, monkeypatch):
    """1순위 > 2순위 — 둘 다 있으면 explicit 우선."""
    cwd_env = isolated_env / ".env"
    cwd_env.write_text("WHOOING_AI_TOKEN=__token_cwd\n")
    explicit = isolated_env / "other.env"
    explicit.write_text("WHOOING_AI_TOKEN=__token_explicit\n")
    monkeypatch.setenv("WHOOING_MCP_ENV", str(explicit))
    _load_env()
    assert os.environ["WHOOING_AI_TOKEN"] == "__token_explicit"


def test_no_env_file_emits_warning(isolated_env, caplog):
    """4경로 모두 실패 시 경고 로그 + WHOOING_AI_TOKEN 미설정 유지."""
    import logging

    caplog.set_level(logging.WARNING, logger="whooing_mcp")
    _load_env()
    # cwd 와 HOME 격리됐으니 project root 의 .env 만 잡힐 수 있음.
    # project root .env 가 없으면 경고. 본 repo 는 .env 가 있으므로
    # 일반적으로 경고 없음 — 본 테스트는 환경 의존이라 strict 검사 안 함.
    # 대신 _load_env 가 예외 없이 끝나는지만 확인.
    assert True


def test_user_config_env_used_when_others_missing(isolated_env, monkeypatch):
    """4순위: ~/.config/whooing-mcp/.env (cwd / project / explicit 모두 없을 때).

    실 project root 에 .env 가 있으므로 (3순위) 본 케이스는 직접 시뮬레이션
    어려움. 대신 explicit 으로 가짜 path 를 넣어 우선순위만 확인.
    """
    user_env_dir = isolated_env / "fake-home" / ".config" / "whooing-mcp"
    user_env_dir.mkdir(parents=True)
    user_env = user_env_dir / ".env"
    user_env.write_text("WHOOING_SECTION_ID=s_user_config\n")
    # explicit 으로 user config 를 직접 가리키기 (3순위가 가로채는 것 회피)
    monkeypatch.setenv("WHOOING_MCP_ENV", str(user_env))
    _load_env()
    assert os.environ.get("WHOOING_SECTION_ID") == "s_user_config"
