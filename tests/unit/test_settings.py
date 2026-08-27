from __future__ import annotations

from pathlib import Path

from hy3_contestlens.settings import AppSettings


def test_secret_file_has_priority_over_environment(tmp_path: Path, monkeypatch):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "secrets.local.toml").write_text('[hy3]\napi_key="file-key"\nbase_url="http://file/v1"\nmodel="file-model"\n', encoding="utf-8")
    monkeypatch.setenv("HY3_API_KEY", "env-key")
    settings = AppSettings.load(tmp_path)
    assert settings.hy3.api_key == "file-key"
    assert settings.hy3.base_url == "http://file/v1"


def test_environment_is_fallback(tmp_path: Path, monkeypatch):
    (tmp_path / "configs").mkdir()
    monkeypatch.setenv("HY3_API_KEY", "env-key")
    monkeypatch.setenv("HY3_BASE_URL", "http://env/v1")
    monkeypatch.setenv("HY3_MODEL", "hy3-test")
    settings = AppSettings.load(tmp_path)
    assert settings.hy3.configured
    assert settings.hy3.api_key == "env-key"

