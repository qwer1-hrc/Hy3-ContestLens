from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ContestLensError(Exception):
    code: str
    message: str
    details: dict[str, Any] | None = None
    status_code: int = 400

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def ensure(condition: bool, code: str, message: str, *, status_code: int = 400, **details: Any) -> None:
    if not condition:
        raise ContestLensError(code, message, details or None, status_code)

