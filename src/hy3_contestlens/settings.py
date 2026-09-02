from __future__ import annotations

import math
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def _toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _value(file_data: dict[str, Any], section: str, name: str, env_name: str, default: Any = None) -> Any:
    configured = file_data.get(section, {}).get(name)
    if configured not in (None, ""):
        return configured
    return os.getenv(env_name, default)


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false", "1", "0"}:
        return value.lower() in {"true", "1"}
    raise ValueError("Boolean setting must be true or false")


@dataclass(slots=True)
class Hy3Settings:
    api_key: str | None = None
    base_url: str = "http://127.0.0.1:8001/v1"
    model: str = "hy3"
    reasoning_effort: str = "medium"
    temperature: float = 0.2
    top_p: float = 0.95
    max_tokens: int = 8192
    timeout_seconds: float = 480
    response_format: str = "json_schema"
    max_attempts: int = 3
    retry_backoff_seconds: float = 1.0
    stream: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.stream, bool):
            raise ValueError("Hy3 stream must be a boolean")
        if self.response_format not in {"json_schema", "json_object"}:
            raise ValueError("Hy3 response_format must be json_schema or json_object")
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or not 1 <= self.max_attempts <= 5:
            raise ValueError("Hy3 max_attempts must be an integer between 1 and 5")
        if isinstance(self.timeout_seconds, bool) or not math.isfinite(self.timeout_seconds) or not 1 <= self.timeout_seconds <= 3600:
            raise ValueError("Hy3 timeout_seconds must be between 1 and 3600")
        if not math.isfinite(self.retry_backoff_seconds) or not 0 <= self.retry_backoff_seconds <= 30:
            raise ValueError("Hy3 retry_backoff_seconds must be between 0 and 30")

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def safe_summary(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "base_url": self.base_url,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "response_format": self.response_format,
            "timeout_seconds": self.timeout_seconds,
            "max_attempts": self.max_attempts,
            "stream": self.stream,
        }


@dataclass(slots=True)
class ReportTranslationSettings:
    """Report-only budget. Only connection credentials/model inherit from Hy3."""
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    reasoning_effort: str = "low"
    temperature: float = 0.1
    top_p: float = 0.95
    max_tokens: int = 4096
    response_format: str | None = None
    timeout_seconds: float = 60
    max_attempts: int = 3
    retry_backoff_seconds: float = 1.0
    batch_max_items: int = 2
    batch_max_chars: int = 2000
    max_concurrency: int = 2

    def __post_init__(self) -> None:
        for name, minimum, maximum in (
            ("max_tokens", 256, 16384), ("max_attempts", 1, 5),
            ("batch_max_items", 1, 8), ("batch_max_chars", 2000, 6000),
            ("max_concurrency", 1, 8),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"Report translation {name} must be an integer between {minimum} and {maximum}")
        for name, minimum, maximum in (
            ("temperature", 0, 2), ("top_p", 0.01, 1),
            ("timeout_seconds", 1, 480), ("retry_backoff_seconds", 0, 30),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError(f"Report translation {name} must be between {minimum} and {maximum}")
        if self.reasoning_effort not in {"", "low", "medium", "high"}:
            raise ValueError("Report translation reasoning_effort must be low, medium, high or empty")
        if self.response_format not in {None, "json_schema", "json_object"}:
            raise ValueError("Report translation response_format must be json_schema or json_object")

    def model_settings(self, solver: Hy3Settings) -> Hy3Settings:
        return Hy3Settings(
            api_key=self.api_key or solver.api_key,
            base_url=self.base_url or solver.base_url,
            model=self.model or solver.model,
            reasoning_effort=self.reasoning_effort,
            temperature=self.temperature, top_p=self.top_p, max_tokens=self.max_tokens,
            response_format=self.response_format or solver.response_format,
            timeout_seconds=self.timeout_seconds,
            # The report queue owns fragment-level retries; avoid nested whole-batch retries.
            max_attempts=1, retry_backoff_seconds=0,
            stream=False,
        )


@dataclass(slots=True)
class ImageUnderstandingSettings:
    """Optional vision connection. Never inherits credentials or parameters from Hy3."""
    api_key: str | None = None
    base_url: str = "https://api.moonshot.cn/v1"
    model: str = "kimi-k3"
    max_tokens: int = 8192
    timeout_seconds: float = 90
    decision_timeout_seconds: float = 300
    max_images: int = 12
    max_image_mb: int = 8
    max_image_side: int = 2400
    max_output_chars: int = 16000
    configuration_error: bool = False

    def __post_init__(self) -> None:
        if self.api_key is not None and not isinstance(self.api_key, str):
            raise ValueError("Image understanding api_key must be a string")
        if not isinstance(self.model, str):
            raise ValueError("Image understanding model must be a string")
        endpoint = urlsplit(self.base_url)
        if endpoint.scheme not in {"http", "https"} or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError("Image understanding base_url must be an HTTP(S) endpoint without credentials or query")
        for name, minimum, maximum in (
            ("max_tokens", 256, 32768), ("max_images", 1, 32), ("max_image_mb", 1, 20),
            ("max_image_side", 512, 4096), ("max_output_chars", 256, 32000),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"Image understanding {name} is out of range")
        for name, maximum in (("timeout_seconds", 480), ("decision_timeout_seconds", 3600)):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or not 1 <= value <= maximum:
                raise ValueError(f"Image understanding {name} is out of range")

    @property
    def configured(self) -> bool:
        return bool(not self.configuration_error and self.api_key and self.api_key.strip() and self.model.strip())

    def safe_summary(self) -> dict[str, Any]:
        return {"configured": self.configured, "model": self.model,
                "configuration_error": self.configuration_error,
                "decision_timeout_seconds": self.decision_timeout_seconds}


@dataclass(slots=True)
class ResourceSettings:
    allow_local_webui_grants: bool = True
    auto_discover: bool = True
    max_depth: int = 8
    max_entries: int = 20_000
    max_document_mb: int = 50
    max_return_chars: int = 12_000
    auto_bind_confidence: float = 0.90
    roots: list[Path] = field(default_factory=list)


@dataclass(slots=True)
class AppSettings:
    project_root: Path
    host: str = "127.0.0.1"
    port: int = 8000
    local_mode: bool = True
    database_path: Path | None = None
    runs_root: Path | None = None
    private_data_root: Path | None = None
    manifests_root: Path | None = None
    default_memory_mb: int = 512
    max_source_bytes: int = 1_048_576
    max_output_bytes: int = 16_777_216
    repair_max_rounds: int = 3
    repair_hard_max_rounds: int = 5
    stop_after_no_improvement_rounds: int = 2
    docker_executable: str = "docker"
    docker_compile_image: str = "hy3-contestlens-compile:local"
    docker_run_image: str = "hy3-contestlens-run:local"
    compile_timeout_seconds: int = 30
    hy3: Hy3Settings = field(default_factory=Hy3Settings)
    report_translation: ReportTranslationSettings = field(default_factory=ReportTranslationSettings)
    image_understanding: ImageUnderstandingSettings = field(default_factory=ImageUnderstandingSettings)
    resources: ResourceSettings = field(default_factory=ResourceSettings)

    def __post_init__(self) -> None:
        root = self.project_root.resolve()
        self.project_root = root
        self.database_path = (self.database_path or root / "runs" / "contestlens.sqlite3").resolve()
        self.runs_root = (self.runs_root or root / "runs").resolve()
        self.private_data_root = (self.private_data_root or root / "data" / "private").resolve()
        self.manifests_root = (self.manifests_root or root / "data" / "manifests").resolve()

    @classmethod
    def load(cls, project_root: Path | None = None) -> "AppSettings":
        configured_root = project_root or Path(os.getenv("HY3_CONTESTLENS_ROOT", ""))
        if not str(configured_root):
            configured_root = Path(__file__).resolve().parents[2]
        root = configured_root.resolve()
        app_file = _toml(root / "configs" / "app.toml") or _toml(root / "configs" / "app.example.toml")
        resource_file = _toml(root / "configs" / "resources.toml") or _toml(root / "configs" / "resources.example.toml")
        secret_file = _toml(root / "configs" / "secrets.local.toml")
        app = app_file.get("app", {})
        repair = app_file.get("repair", {})
        docker = app_file.get("docker", {})
        resource = resource_file.get("resources", {})
        def app_path(environment: str, configured: str) -> Path:
            raw = os.getenv(environment) or configured
            path = Path(raw)
            return path if path.is_absolute() else root / path

        roots: list[Path] = []
        for item in resource_file.get("resources", {}).get("roots", []):
            raw = item.get("path", "")
            if raw and not raw.startswith("<"):
                roots.append(Path(raw).resolve())
        for item in resource_file.get("resources_roots", []):
            raw = item.get("path", "")
            if raw and not raw.startswith("<"):
                roots.append(Path(raw).resolve())
        hy3 = Hy3Settings(
            api_key=_value(secret_file, "hy3", "api_key", "HY3_API_KEY"),
            base_url=str(_value(secret_file, "hy3", "base_url", "HY3_BASE_URL", "http://127.0.0.1:8001/v1")),
            model=str(_value(secret_file, "hy3", "model", "HY3_MODEL", "hy3")),
            reasoning_effort=str(_value(secret_file, "hy3", "reasoning_effort", "HY3_REASONING_EFFORT", "medium")),
            temperature=float(_value(secret_file, "hy3", "temperature", "HY3_TEMPERATURE", 0.2)),
            top_p=float(_value(secret_file, "hy3", "top_p", "HY3_TOP_P", 0.95)),
            max_tokens=int(_value(secret_file, "hy3", "max_tokens", "HY3_MAX_TOKENS", 8192)),
            timeout_seconds=float(_value(secret_file, "hy3", "timeout_seconds", "HY3_TIMEOUT_SECONDS", 480)),
            response_format=str(_value(secret_file, "hy3", "response_format", "HY3_RESPONSE_FORMAT", "json_schema")),
            max_attempts=int(_value(secret_file, "hy3", "max_attempts", "HY3_MAX_ATTEMPTS", 3)),
            retry_backoff_seconds=float(_value(secret_file, "hy3", "retry_backoff_seconds", "HY3_RETRY_BACKOFF_SECONDS", 1.0)),
            stream=_boolean(_value(secret_file, "hy3", "stream", "HY3_STREAM", True)),
        )
        report_data = {"report_translation": {
            **app_file.get("report_translation", {}), **secret_file.get("report_translation", {}),
        }}

        def report_value(name: str, default: Any = None) -> Any:
            return _value(report_data, "report_translation", name, f"HY3_REPORT_{name.upper()}", default)

        report_concurrency = report_value("max_concurrency", 2)
        report_translation = ReportTranslationSettings(
            api_key=report_value("api_key"), base_url=report_value("base_url"), model=report_value("model"),
            reasoning_effort=str(report_value("reasoning_effort", "low")),
            temperature=float(report_value("temperature", 0.1)), top_p=float(report_value("top_p", 0.95)),
            max_tokens=int(report_value("max_tokens", 4096)), response_format=report_value("response_format"),
            timeout_seconds=float(report_value("timeout_seconds", 60)),
            max_attempts=int(report_value("max_attempts", 3)),
            retry_backoff_seconds=float(report_value("retry_backoff_seconds", 1.0)),
            batch_max_items=int(report_value("batch_max_items", 2)),
            batch_max_chars=int(report_value("batch_max_chars", 2000)),
            max_concurrency=int(report_concurrency) if isinstance(report_concurrency, str) else report_concurrency,
        )
        def image_value(name: str, default: Any = None) -> Any:
            return _value(image_data, "image_understanding", name, f"HY3_IMAGE_{name.upper()}", default)

        try:
            image_data = {"image_understanding": {
                **app_file.get("image_understanding", {}), **secret_file.get("image_understanding", {}),
            }}
            image_understanding = ImageUnderstandingSettings(
                api_key=image_value("api_key"), base_url=str(image_value("base_url", "https://api.moonshot.cn/v1")),
                model=str(image_value("model", "kimi-k3")),
                **{name: int(image_value(name, default)) for name, default in (
                    ("max_tokens", 8192), ("max_images", 12), ("max_image_mb", 8),
                    ("max_image_side", 2400), ("max_output_chars", 16000),
                )},
                timeout_seconds=float(image_value("timeout_seconds", 90)),
                decision_timeout_seconds=float(image_value("decision_timeout_seconds", 300)),
            )
        except (ValueError, TypeError, AttributeError):
            # A bad optional profile must not disable the solver or readiness checks.
            image_understanding = ImageUnderstandingSettings(configuration_error=True)
        return cls(
            project_root=root,
            host=str(app.get("host", "127.0.0.1")),
            port=int(app.get("port", 8000)),
            local_mode=bool(app.get("local_mode", True)),
            database_path=app_path("HY3_CONTESTLENS_DATABASE_PATH", str(app.get("database_path", "runs/contestlens.sqlite3"))),
            runs_root=app_path("HY3_CONTESTLENS_RUNS_ROOT", str(app.get("runs_root", "runs"))),
            default_memory_mb=int(app.get("default_memory_mb", 512)),
            max_source_bytes=int(app.get("max_source_bytes", 1_048_576)),
            max_output_bytes=int(app.get("max_output_bytes", 16_777_216)),
            repair_max_rounds=int(repair.get("max_rounds", 3)),
            repair_hard_max_rounds=int(repair.get("hard_max_rounds", 5)),
            stop_after_no_improvement_rounds=int(repair.get("stop_after_no_improvement_rounds", 2)),
            docker_executable=str(docker.get("executable", "docker")),
            docker_compile_image=str(docker.get("compile_image", "hy3-contestlens-compile:local")),
            docker_run_image=str(docker.get("run_image", "hy3-contestlens-run:local")),
            compile_timeout_seconds=int(docker.get("compile_timeout_seconds", 30)),
            hy3=hy3,
            report_translation=report_translation,
            image_understanding=image_understanding,
            resources=ResourceSettings(
                allow_local_webui_grants=bool(resource.get("allow_local_webui_grants", True)),
                auto_discover=bool(resource.get("auto_discover", True)),
                max_depth=int(resource.get("max_depth", 8)),
                max_entries=int(resource.get("max_entries", 20_000)),
                max_document_mb=int(resource.get("max_document_mb", 50)),
                max_return_chars=int(resource.get("max_return_chars", 12_000)),
                auto_bind_confidence=float(resource.get("auto_bind_confidence", 0.90)),
                roots=roots,
            ),
        )
