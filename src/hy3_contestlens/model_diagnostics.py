from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .utils import atomic_write_json


logger = logging.getLogger(__name__)
MAX_RESPONSE_LOG_CHARS = 2_000_000
_SENSITIVE_KEYS = {
    "authorization", "proxyauthorization", "apikey", "key", "token", "accesstoken",
    "refreshtoken", "password", "secret", "clientsecret", "cookie", "setcookie",
}
_REASONING_KEYS = {"reasoningcontent", "reasoning", "thinking", "thoughts"}


@dataclass(frozen=True)
class ModelRunContext:
    run_id: str
    directory: Path
    cancelled: Callable[[], bool] | None = None
    progress: Callable[[dict[str, Any]], None] | None = None

    def emit_progress(self, data: dict[str, Any]) -> None:
        if self.progress is not None:
            try:
                self.progress(data)
            except Exception:
                logger.warning("Model progress update failed", exc_info=False)

    def check_cancelled(self) -> None:
        if self.cancelled is not None and self.cancelled():
            raise asyncio.CancelledError("Run cancelled before another model request")


current_model_run: ContextVar[ModelRunContext | None] = ContextVar("model_run", default=None)


@contextmanager
def model_run_context(
    runs_root: Path, run_id: str, cancelled: Callable[[], bool] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> Iterator[ModelRunContext]:
    if not re.fullmatch(r"run_[A-Za-z0-9_-]+", run_id):
        raise ValueError("Invalid run ID for model diagnostics")
    root = runs_root.resolve()
    directory = (root / run_id / "model_calls").resolve()
    if not directory.is_relative_to(root):
        raise ValueError("Model diagnostics must stay inside runs_root")
    context = ModelRunContext(run_id, directory, cancelled, progress)
    token = current_model_run.set(context)
    try:
        yield context
    finally:
        current_model_run.reset(token)


def safe_endpoint(endpoint: str) -> str:
    """Do not retain URL credentials, query-string credentials or fragments."""
    parts = urlsplit(endpoint)
    return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))


def redact_text(text: str, api_key: str | None) -> str:
    if api_key:
        for value in {api_key, json.dumps(api_key)[1:-1]}:
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)\bBearer\s+[^\s\"'<>\\]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    text = re.sub(
        r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization)[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)",
        r"\1\2[REDACTED]\2", text,
    )
    return re.sub(
        r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|token)=)[^&\s\"'<>]+",
        r"\1[REDACTED]", text,
    )


def redact(value: Any, api_key: str | None) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized in _SENSITIVE_KEYS:
                item = "[REDACTED]"
            elif normalized in _REASONING_KEYS:
                item = "[OMITTED]"
            else:
                item = redact(item, api_key)
            cleaned[redact_text(str(key), api_key)] = item
        return cleaned
    if isinstance(value, (list, tuple)):
        return [redact(item, api_key) for item in value]
    if isinstance(value, str):
        # Completion content is commonly a JSON-encoded string, not an object.
        if value.lstrip().startswith(("{", "[")):
            try:
                return json.dumps(redact(json.loads(value), api_key), ensure_ascii=False)
            except (ValueError, RecursionError):
                pass
        return redact_text(value, api_key)
    return value


def write_attempt(
    directory: Path | None, filename: str, record: dict[str, Any], api_key: str | None,
) -> str | None:
    """Logging failures must not mask the original provider/validation outcome."""
    if directory is None:
        return None
    try:
        atomic_write_json(directory / filename, redact(record, api_key))
    except (OSError, ValueError, RecursionError) as exc:
        error_type = type(exc).__name__
        logger.warning("Model diagnostics write failed (%s)", error_type)
        return error_type
    return None
