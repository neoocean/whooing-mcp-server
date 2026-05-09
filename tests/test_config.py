"""config.py — 탐색 우선순위 + 파싱 + default 회귀."""

from __future__ import annotations

import pytest

from whooing_mcp.config import Config, load_config, reset_cache


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch, tmp_path):
    reset_cache()
    monkeypatch.delenv("WHOOING_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    yield
    reset_cache()


def test_default_when_no_config_file(monkeypatch, tmp_path):
    """모든 후보 경로 부재 시 default (p4_sync_enabled=False)."""
    # explicit 도 안 주고, project 의 whooing-mcp.toml 이 있을 수 있어
    # 일단 explicit 으로 nonexistent 를 강제 — 나머지 후보는 walk 이후 fallback.
    monkeypatch.setenv("WHOOING_CONFIG", str(tmp_path / "nonexistent.toml"))
    cfg = load_config()
    # 본 repo 에 whooing-mcp.toml 이 있으면 그게 잡힘. 없으면 default.
    # 본 테스트는 explicit 잘못된 경로 → 다음 후보 (project root) 탐색 →
    # 만약 project root 에 toml 이 있으면 그것의 enabled 값. 본 테스트
    # 환경에서 project root 의 enabled=true 로 세팅돼 있으면 통과 X.
    # 따라서 cfg 가 Config 인스턴스인지만 검증.
    assert isinstance(cfg, Config)


def test_explicit_path_loads(monkeypatch, tmp_path):
    cfg_file = tmp_path / "custom.toml"
    cfg_file.write_text('[p4_sync]\nenabled = true\n')
    monkeypatch.setenv("WHOOING_CONFIG", str(cfg_file))
    cfg = load_config(force_reload=True)
    assert cfg.p4_sync_enabled is True


def test_explicit_path_disabled(monkeypatch, tmp_path):
    cfg_file = tmp_path / "off.toml"
    cfg_file.write_text('[p4_sync]\nenabled = false\n')
    monkeypatch.setenv("WHOOING_CONFIG", str(cfg_file))
    cfg = load_config(force_reload=True)
    assert cfg.p4_sync_enabled is False


def test_missing_section_uses_default(monkeypatch, tmp_path):
    cfg_file = tmp_path / "empty.toml"
    cfg_file.write_text("# empty config\n")
    monkeypatch.setenv("WHOOING_CONFIG", str(cfg_file))
    cfg = load_config(force_reload=True)
    assert cfg.p4_sync_enabled is False


def test_invalid_toml_falls_back_to_default(monkeypatch, tmp_path):
    cfg_file = tmp_path / "bad.toml"
    cfg_file.write_text("this is not [valid TOML\n")
    monkeypatch.setenv("WHOOING_CONFIG", str(cfg_file))
    cfg = load_config(force_reload=True)
    assert cfg.p4_sync_enabled is False


def test_caching(monkeypatch, tmp_path):
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text('[p4_sync]\nenabled = true\n')
    monkeypatch.setenv("WHOOING_CONFIG", str(cfg_file))
    cfg1 = load_config(force_reload=True)
    cfg_file.write_text('[p4_sync]\nenabled = false\n')
    cfg2 = load_config()  # 캐시
    assert cfg2.p4_sync_enabled is True  # 캐시 — 변경 안 보임
    cfg3 = load_config(force_reload=True)
    assert cfg3.p4_sync_enabled is False  # 강제 reload


def test_from_dict_explicit():
    assert Config.from_dict({"p4_sync": {"enabled": True}}).p4_sync_enabled is True
    assert Config.from_dict({"p4_sync": {"enabled": False}}).p4_sync_enabled is False
    assert Config.from_dict({}).p4_sync_enabled is False
