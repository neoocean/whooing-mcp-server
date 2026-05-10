"""pytest 공통 fixture — 모든 테스트에 적용 (autouse).

핵심 격리 정책 (v0.1.12):
  * 실 P4 호출 금지 — 머신에 `whooing-mcp.toml [p4_sync] enabled=true` 가
    있으면 (실 사용자 머신 default) 테스트 도중 sync_paths_to_p4 가 fire 해
    실 P4 서버에 빈 CL 을 leak 시킬 수 있다 (검증: 2026-05-10, 60+ 빈 CL
    leak). conftest 에서 default 로 disabled 강제.
  * test_p4_sync.py 는 자체 fixture 로 명시적 enabled 처리 — 본 default 와
    충돌하지 않음 (test fixture 가 더 좁은 scope 라 우선).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def disable_p4_sync_globally(monkeypatch, tmp_path):
    """실 머신의 whooing-mcp.toml 이 enabled=true 여도 테스트 한정으로 false.

    수단: WHOOING_CONFIG 환경변수를 빈 toml 로 덮어 쓴다. config 모듈이
    이 경로를 우선 보고 → p4_sync.enabled = false (default).
    """
    from whooing_mcp import config as config_mod
    config_mod.reset_cache()
    cfg_file = tmp_path / "_disabled_p4_sync.toml"
    cfg_file.write_text("# tests: empty config → p4_sync disabled\n")
    monkeypatch.setenv("WHOOING_CONFIG", str(cfg_file))
    yield
    config_mod.reset_cache()
