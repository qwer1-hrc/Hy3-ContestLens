from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from typing import Any


class StreamResponseError(Exception):
    def __init__(self, kind: str, reason: str):
        super().__init__(reason)
        self.kind = kind


class RepetitionGuard:
    """Conservative answer-only guard for prose reviews, never solver code or reasoning.

    Require a long tail dominated by repeated complete sentences. Short recurring
    schema keys, step verdicts and mathematical expressions cannot trigger it.
    """

    def __init__(self) -> None:
        self.tail = ""
        self.since_check = 0

    def feed(self, text: str) -> None:
        self.tail = (self.tail + text)[-8192:]
        self.since_check += len(text)
        if len(self.tail) < 8192 or self.since_check < 1024:
            return
        self.since_check = 0
        sentences = re.findall(r"[^.!?。！？\n]{12,300}[.!?。！？]", self.tail)
        counts = Counter(s.strip() for s in sentences)
        covered = sum(len(s) * n for s, n in counts.items())
        repeated = sum(len(s) * n for s, n in counts.items() if n >= 4)
        if (len(sentences) >= 40 and covered >= 6000 and repeated >= covered * .85
                and len(counts) <= len(sentences) * .25):
            raise StreamResponseError("repetitive_output", "Review answer entered a sustained repetitive loop")


class CompletionStream:
    """Assemble SSE answer text, never store or expose private reasoning deltas."""

    def __init__(self, *, guard_repetition: bool = False) -> None:
        self.started = time.monotonic()
        self.first_event_ms: int | None = None
        self.event_count = 0
        self.reasoning_chars = 0
        self.content_chars = 0
        self.done = False
        self.finish_reason: str | None = None
        self.metadata: dict[str, Any] = {}
        self.error: Any = None
        self.refusal: str | None = None
        self._parts: list[str] = []
        self._data: list[str] = []
        self._event_chars = 0
        self._wire_chars = 0
        self._hash = hashlib.sha256()
        self._repetition_guard = RepetitionGuard() if guard_repetition else None

    def feed(self, line: str) -> None:
        self._hash.update((line + "\n").encode("utf-8"))
        self._wire_chars += len(line)
        if self._wire_chars > 64_000_000 or len(line) > 1_000_000:
            raise StreamResponseError("response_shape", "Streaming response exceeded the safety limit")
        if line.startswith("data:"):
            value = line[5:].removeprefix(" ")
            self._event_chars += len(value)
            if self._event_chars > 1_000_000:
                raise StreamResponseError("response_shape", "SSE event exceeded the safety limit")
            self._data.append(value)
        elif not line and self._data:
            value = "\n".join(self._data)
            self._data.clear()
            self._event_chars = 0
            self._event(value)
        # Comments (heartbeats), event IDs and event names carry no answer text.

    def _event(self, value: str) -> None:
        if value.strip() == "[DONE]":
            self.done = True
            return
        try:
            chunk = json.loads(value)
        except (ValueError, TypeError):
            raise StreamResponseError("response_shape", "SSE event is not a valid JSON object") from None
        if not isinstance(chunk, dict):
            raise StreamResponseError("response_shape", "SSE event must be a JSON object")
        self.event_count += 1
        if self.first_event_ms is None:
            self.first_event_ms = round((time.monotonic() - self.started) * 1000)
        for key in ("id", "model", "usage"):
            if chunk.get(key) is not None:
                self.metadata[key] = chunk[key]
        if "error" in chunk:
            self.error = chunk["error"]
            raise StreamResponseError("stream_error", "Provider returned an error event during streaming")
        choices = chunk.get("choices", [])
        if not isinstance(choices, list):
            raise StreamResponseError("response_shape", "Streaming choices must be an array")
        for choice in choices:
            if not isinstance(choice, dict):
                raise StreamResponseError("response_shape", "Streaming choice must be an object")
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta", {})
            if not isinstance(delta, dict):
                raise StreamResponseError("response_shape", "Streaming delta must be an object")
            if "error" in delta:
                self.error = delta["error"]
                raise StreamResponseError("stream_error", "Provider returned an error delta during streaming")
            if delta.get("tool_calls"):
                raise StreamResponseError("response_shape", "Unexpected tool call in a JSON-only response")
            if delta.get("refusal"):
                self.refusal = "Provider declined to produce a response"
            reasoning = delta.get("reasoning_content")
            if isinstance(reasoning, str):
                self.reasoning_chars += len(reasoning)
            content = delta.get("content")
            if content is not None:
                if not isinstance(content, str):
                    raise StreamResponseError("response_shape", "Streaming answer content must be text")
                self.content_chars += len(content)
                if self.content_chars > 4_000_000:
                    raise StreamResponseError("response_shape", "Streaming answer exceeded the safety limit")
                self._parts.append(content)
                if self._repetition_guard:
                    self._repetition_guard.feed(content)
            if choice.get("finish_reason") is not None:
                self.finish_reason = choice["finish_reason"]
            # Kimi places usage on the terminal choice, while OpenAI-compatible
            # providers may place it at the chunk root.
            if choice.get("usage") is not None:
                self.metadata["usage"] = choice["usage"]

    def finish(self) -> None:
        # SSE events are dispatched at blank lines. EOF is not a successful completion.
        if not self.done or self.finish_reason is None:
            raise StreamResponseError("stream_incomplete", "Model stream ended without a finish reason and [DONE] marker")

    def body(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": "".join(self._parts)}
        if self.refusal:
            message["refusal"] = self.refusal
        if self.reasoning_chars:
            message["reasoning_content"] = "[OMITTED]"
        return {**self.metadata, "choices": [{"index": 0, "message": message, "finish_reason": self.finish_reason}],
                **({"error": self.error} if self.error is not None else {})}

    def diagnostics(self) -> dict[str, Any]:
        return {"event_count": self.event_count, "first_event_ms": self.first_event_ms,
                "content_chars": self.content_chars, "reasoning_chars": self.reasoning_chars,
                "done": self.done, "wire_lines_sha256": self._hash.hexdigest()}
