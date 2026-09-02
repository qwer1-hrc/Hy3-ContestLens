from __future__ import annotations

from pathlib import Path

import pytest

from hy3_contestlens.settings import AppSettings, Hy3Settings, ReportTranslationSettings


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


def test_runtime_database_and_runs_paths_can_be_isolated(tmp_path: Path, monkeypatch):
    database = tmp_path / "state" / "isolated.sqlite3"
    runs = tmp_path / "artifacts"
    monkeypatch.setenv("HY3_CONTESTLENS_DATABASE_PATH", str(database))
    monkeypatch.setenv("HY3_CONTESTLENS_RUNS_ROOT", str(runs))
    settings = AppSettings.load(Path(__file__).resolve().parents[2])
    assert settings.database_path == database.resolve()
    assert settings.runs_root == runs.resolve()


def test_streaming_is_configurable_without_changing_solver_budget(tmp_path, monkeypatch):
    assert Hy3Settings().stream is True
    monkeypatch.setenv("HY3_STREAM", "false")
    monkeypatch.setenv("HY3_MAX_TOKENS", "127000")
    monkeypatch.setenv("HY3_REASONING_EFFORT", "high")
    settings = AppSettings.load(tmp_path)
    assert settings.hy3.stream is False
    assert settings.hy3.max_tokens == 127000 and settings.hy3.reasoning_effort == "high"
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/secrets.local.toml").write_text('[hy3]\nstream=true\n', encoding="utf-8")
    assert AppSettings.load(tmp_path).hy3.stream is True
    assert ReportTranslationSettings().model_settings(Hy3Settings()).stream is False


def test_model_reliability_defaults_and_environment(tmp_path, monkeypatch):
    assert Hy3Settings().response_format == "json_schema"
    assert Hy3Settings().max_attempts == 3
    assert Hy3Settings().timeout_seconds == 480
    monkeypatch.setenv("HY3_RESPONSE_FORMAT", "json_object")
    monkeypatch.setenv("HY3_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("HY3_RETRY_BACKOFF_SECONDS", "0.5")
    monkeypatch.setenv("HY3_TIMEOUT_SECONDS", "1200")
    settings = AppSettings.load(tmp_path)
    assert settings.hy3.response_format == "json_object"
    assert settings.hy3.max_attempts == 2
    assert settings.hy3.retry_backoff_seconds == 0.5
    assert settings.hy3.timeout_seconds == 1200


@pytest.mark.parametrize("options", [
    {"response_format": "text"}, {"max_attempts": 0}, {"max_attempts": 6},
    {"max_attempts": True}, {"max_attempts": 1.5},
    {"timeout_seconds": 0}, {"timeout_seconds": 3601}, {"timeout_seconds": True},
    {"timeout_seconds": float("inf")}, {"timeout_seconds": float("nan")},
    {"retry_backoff_seconds": -1}, {"retry_backoff_seconds": 31},
    {"retry_backoff_seconds": float("inf")}, {"retry_backoff_seconds": float("nan")},
])
def test_model_retry_configuration_is_bounded(options):
    with pytest.raises(ValueError):
        Hy3Settings(**options)


def test_report_profile_inherits_connection_but_not_solver_budget():
    solver = Hy3Settings(api_key="solver-key", base_url="https://model.example/v1", model="hy3", reasoning_effort="high", max_tokens=127000)
    options = ReportTranslationSettings()
    report = options.model_settings(solver)
    assert report.api_key == solver.api_key and report.base_url == solver.base_url and report.model == solver.model
    assert report.reasoning_effort == "low" and report.max_tokens == 4096
    assert report.temperature == 0.1 and report.max_attempts == 1
    assert report.timeout_seconds == options.timeout_seconds
    assert options.max_attempts == 3 and options.batch_max_items == 2 and options.batch_max_chars == 2000
    assert options.max_concurrency == 2
    assert solver.reasoning_effort == "high" and solver.max_tokens == 127000
    assert report is not solver


def test_report_profile_loads_app_secret_and_environment_overrides(tmp_path, monkeypatch):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "app.toml").write_text(
        '[report_translation]\nmax_tokens=2048\nbatch_max_items=1\ntimeout_seconds=45\n', encoding="utf-8",
    )
    (tmp_path / "configs" / "secrets.local.toml").write_text(
        '[hy3]\napi_key="solver-key"\nreasoning_effort="high"\nmax_tokens=127000\n'
        '[report_translation]\napi_key="report-key"\nmodel="translator"\nmax_tokens=1024\n', encoding="utf-8",
    )
    monkeypatch.setenv("HY3_REPORT_BASE_URL", "http://translation/v1")
    monkeypatch.setenv("HY3_REPORT_MAX_TOKENS", "512")
    monkeypatch.setenv("HY3_REPORT_RETRY_BACKOFF_SECONDS", "0")
    settings = AppSettings.load(tmp_path)
    report = settings.report_translation.model_settings(settings.hy3)
    assert report.api_key == "report-key" and report.model == "translator"
    assert report.base_url == "http://translation/v1" and report.max_tokens == 1024
    assert settings.report_translation.timeout_seconds == 45
    assert settings.report_translation.batch_max_items == 1
    assert settings.report_translation.retry_backoff_seconds == 0
    assert settings.hy3.api_key == "solver-key" and settings.hy3.max_tokens == 127000


@pytest.mark.parametrize("options", [
    {"max_tokens": 127000}, {"max_tokens": True}, {"max_attempts": 0},
    {"batch_max_items": 0}, {"batch_max_items": 9}, {"batch_max_chars": 1999},
    {"temperature": float("nan")}, {"top_p": 0}, {"timeout_seconds": float("inf")},
    {"retry_backoff_seconds": -1}, {"response_format": "text"}, {"reasoning_effort": "unsupported"},
    {"max_concurrency": 0}, {"max_concurrency": 9}, {"max_concurrency": True}, {"max_concurrency": 1.5},
])
def test_report_profile_rejects_invalid_or_excessive_budgets(options):
    with pytest.raises(ValueError):
        ReportTranslationSettings(**options)


def test_report_concurrency_supports_environment_and_file_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("HY3_REPORT_MAX_CONCURRENCY", "3")
    assert AppSettings.load(tmp_path).report_translation.max_concurrency == 3
    (tmp_path / "configs").mkdir()
    config = tmp_path / "configs" / "app.toml"
    config.write_text("[report_translation]\nmax_concurrency=1\n", encoding="utf-8")
    assert AppSettings.load(tmp_path).report_translation.max_concurrency == 1
    config.write_text("[report_translation]\nmax_concurrency=true\n", encoding="utf-8")
    with pytest.raises(ValueError):
        AppSettings.load(tmp_path)
