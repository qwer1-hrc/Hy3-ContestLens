from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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


@dataclass(slots=True)
class Hy3Settings:
    api_key: str | None = None
    base_url: str = "http://127.0.0.1:8001/v1"
    model: str = "hy3"
    reasoning_effort: str = "medium"
    temperature: float = 0.2
    top_p: float = 0.95
    max_tokens: int = 8192

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def safe_summary(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "base_url": self.base_url,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
        }


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
        )
        return cls(
            project_root=root,
            host=str(app.get("host", "127.0.0.1")),
            port=int(app.get("port", 8000)),
            local_mode=bool(app.get("local_mode", True)),
            database_path=root / str(app.get("database_path", "runs/contestlens.sqlite3")),
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
