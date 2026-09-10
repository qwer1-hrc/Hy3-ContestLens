"""On-demand report localization. Never invoked by the solving workflow."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .errors import ContestLensError
from .model import Hy3Client
from .model_diagnostics import ModelRunContext, current_model_run
from .reporting import has_evaluation_report
from .settings import ReportTranslationSettings
from .store import Store
from .utils import atomic_write_json, canonical_json, sha256_bytes, utc_now


logger = logging.getLogger(__name__)
CACHE_VERSION = 1
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}
NARRATIVE_FIELDS = {
    "evidence", "summary", "repair_suggestion", "root_cause", "repair_scope",
    "required_changes", "regression_risks", "success_criteria",
}
TRANSLATION_LABELS = {
    "EMPTY": "空", "PENDING": "待格式化", "QUEUED": "排队中",
    "TRANSLATING": "格式化中", "READY": "已格式化", "FAILED": "格式化失败",
}
REPORT_LABELS = {
    "COMPLETED": "已完成", "MAX_ROUNDS": "达到最大修复轮数", "STALLED": "无进一步改进",
    "UNRESOLVED": "未确定错误类型", "REPAIR_DISABLED": "未启用修复", "INFRASTRUCTURE_ERROR": "基础设施错误",
    "STATEMENT_MISREAD": "题意误读", "CONSTRAINT_OMISSION": "遗漏约束", "WRONG_ALGORITHM": "算法错误",
    "PROOF_GAP": "证明存在缺口", "CIRCULAR_REASONING": "循环论证", "HALLUCINATED_CLAIM": "缺乏依据的断言",
    "COMPLEXITY_TLE": "时间复杂度超限", "COMPLEXITY_MLE": "空间复杂度超限", "BOUNDARY_ERROR": "边界错误",
    "INTEGER_OVERFLOW": "整数溢出", "STATE_TRANSITION_ERROR": "状态转移错误", "IMPLEMENTATION_MISMATCH": "实现与算法不一致",
    "IO_OR_FORMAT_ERROR": "输入输出或格式错误", "COMPILE_ERROR": "编译错误", "RUNTIME_ERROR": "运行错误",
    "RESULT_CORRECT_PROCESS_INVALID": "结果正确但过程无效", "SUPPORTED": "有证据支持",
    "UNSUPPORTED": "缺乏支持", "CONTRADICTED": "存在矛盾", "NOT_ASSESSABLE": "无法评估",
}
JUDGE_VERDICT_LABELS = {
    "OK": "编译通过", "AC": "答案正确", "CE": "编译错误", "WA": "答案错误",
    "TLE": "超出时间限制", "MLE": "超出内存限制", "RE": "运行错误",
    "OLE": "超出输出限制", "IO_CONFLICT": "输入输出冲突",
    "SANDBOX_VIOLATION": "违反沙箱限制", "INVALID_PROBLEM_MANIFEST": "题目配置无效",
    "SANDBOX_UNAVAILABLE": "沙箱不可用",
}


def machine_marker_text(value: str) -> str | None:
    """Localize only whole, recognized machine markers, never arbitrary prose/code."""
    match = re.fullmatch(r"deterministic_judge_verdict\s*=\s*([A-Z][A-Z0-9_]*)", value.strip())
    if match is None:
        return None
    verdict = match.group(1)
    return f"确定性判题结果：{JUDGE_VERDICT_LABELS.get(verdict, '未知状态')}（{verdict}）"


def text_key(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def _read_object(path: Path, root: Path) -> dict[str, Any] | None:
    if not path.resolve().is_relative_to(root.resolve()):
        raise ContestLensError("REPORT_PATH_INVALID", "Report artifact is outside its run directory", status_code=400)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def report_sections(result: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    """Read only public diagnosis/review prose; never send source or hidden tests."""
    initial = result["initial_submission_result"]
    critics = []
    for label, field, filename in (
        ("算法 Critic", "algorithm_review", "algorithm_critic.json"),
        ("代码 Critic", "code_review", "code_critic.json"),
        ("代码 Critic 复核前", "code_review_initial", "code_critic_initial.json"),
    ):
        review = initial.get(field) or _read_object(root / filename, root)
        if isinstance(review, dict):
            critics.append({"label": label, "review": review})
    sections = [{"label": "初次提交", "revision_id": initial.get("revision_id"), "diagnosis": initial["diagnosis"], "critics": critics}]
    for number, round_record in enumerate(result.get("repair_round_results") or [], 1):
        reviews = []
        for label, field in (("算法 Critic", "algorithm_review"), ("代码 Critic", "code_review"), ("代码 Critic 复核前", "code_review_initial")):
            if isinstance(round_record.get(field), dict):
                reviews.append({"label": label, "review": round_record[field]})
        sections.append({
            "label": f"修复第 {number} 轮", "revision_id": round_record.get("new_revision_id"),
            "reasoning_revision_id": round_record.get("reasoning_revision_id"),
            "proof_only": round_record.get("proof_only", False),
            "diagnosis": round_record.get("process_evaluation") or {}, "critics": reviews,
            "repair_plan": round_record.get("repair_plan") or {},
            "quality_gate": round_record.get("quality_gate") or {},
        })
    best = result["best_submission_result"]
    best_critics = next((section["critics"] for section in sections if section["revision_id"] == best.get("revision_id")
                         and section.get("reasoning_revision_id") == best.get("reasoning_revision_id")), [])
    return [{"label": "最佳提交", "revision_id": best.get("revision_id"), "diagnosis": best["diagnosis"], "critics": best_critics}, *sections]


def narrative_texts(value: Any, field: str = "") -> dict[str, str]:
    texts: dict[str, str] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            texts.update(narrative_texts(item, key))
    elif isinstance(value, list):
        for item in value:
            texts.update(narrative_texts(item, field))
    elif isinstance(value, str) and value.strip() and field in NARRATIVE_FIELDS:
        if machine_marker_text(value) is None:
            texts[text_key(value)] = value
    return texts


def localize(value: Any, translations: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: localize(item, translations) for key, item in value.items()}
    if isinstance(value, list):
        return [localize(item, translations) for item in value]
    if isinstance(value, str):
        # Deterministic facts take precedence even over an older model-generated cache.
        marker = machine_marker_text(value)
        if marker is not None:
            return marker
        return translations.get(text_key(value), value)
    return value


def _retryable_translation_error(error: ContestLensError) -> bool:
    if error.code != "HY3_INVALID_RESPONSE":
        return False
    details = error.details if isinstance(error.details, dict) else {}
    if details.get("failure_kind") == "refusal":
        return False
    if details.get("failure_kind") == "http_error":
        status = details.get("http_status")
        return isinstance(status, int) and (status in {408, 429} or 500 <= status <= 599)
    return True


class ReportTranslationService:
    def __init__(self, runs_root: Path, store: Store, model: Hy3Client, options: ReportTranslationSettings | None = None):
        self.runs_root = runs_root
        self.store = store
        self.model = model
        self.options = options or ReportTranslationSettings()
        self._pending: list[str] = []
        self._current: str | None = None
        self._worker: asyncio.Task | None = None
        self._errors: dict[str, str] = {}
        self._rate_limit_until = 0.0

    def _root(self, run_id: str) -> Path:
        if not re.fullmatch(r"run_[A-Za-z0-9_-]+", run_id):
            raise ContestLensError("INVALID_IDENTIFIER", "Invalid run identifier", status_code=400)
        root = (self.runs_root / run_id).resolve()
        if not root.is_relative_to(self.runs_root.resolve()):
            raise ContestLensError("REPORT_PATH_INVALID", "Invalid run directory", status_code=400)
        return root

    def _source(self, run_id: str) -> tuple[list[dict[str, Any]], str] | None:
        run = self.store.get_run(run_id)
        if run["status"] not in TERMINAL_STATUSES or not has_evaluation_report(run["result"]):
            return None
        sections = report_sections(run["result"], self._root(run_id))
        return sections, sha256_bytes(canonical_json(sections).encode("utf-8"))

    def _path(self, run_id: str) -> Path:
        root = self._root(run_id)
        path = root / "report_translation" / "zh-CN.json"
        if not path.resolve().is_relative_to(root):
            raise ContestLensError("REPORT_PATH_INVALID", "Invalid report cache directory", status_code=400)
        return path

    def _cache(self, run_id: str, source_hash: str) -> dict[str, Any]:
        cache = _read_object(self._path(run_id), self._root(run_id)) or {}
        if cache.get("schema_version") != CACHE_VERSION or cache.get("source_sha256") != source_hash:
            return {}
        return cache

    def status(self, run_id: str) -> dict[str, Any]:
        source = self._source(run_id)
        cache = self._cache(run_id, source[1]) if source else {}
        state = "PENDING" if source else "EMPTY"
        if source and cache.get("status") == "READY":
            translations = cache.get("translations")
            required = narrative_texts(source[0])
            if isinstance(translations, dict) and all(isinstance(translations.get(key), str) and translations[key].strip() for key in required):
                state = "READY"
        if state == "PENDING" and (cache.get("status") == "FAILED" or run_id in self._errors):
            state = "FAILED"
        if run_id == self._current:
            state = "TRANSLATING"
        elif run_id in self._pending:
            state = "QUEUED"
        return {
            "run_id": run_id, "status": state, "label": TRANSLATION_LABELS[state],
            "processed": state == "READY", "updated_at": cache.get("updated_at"),
            "position": self._pending.index(run_id) + 1 if run_id in self._pending else None,
            "error_code": self._errors.get(run_id) or cache.get("error_code"),
            "completed_segments": len(cache["segments"]) if isinstance(cache.get("segments"), dict) else 0,
            "total_segments": cache.get("total_segments", 0),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "active_run_id": self._current,
            "busy": bool(self._current or self._pending),
            "runs": [self.status(run["run_id"]) for run in self.store.list_runs()],
        }

    def document(self, run_id: str) -> dict[str, Any]:
        state = self.status(run_id)
        source = self._source(run_id)
        sections = []
        if source and state["status"] == "READY":
            sections = localize(source[0], self._cache(run_id, source[1])["translations"])
        return {"state": state, "sections": sections}

    def enqueue(self, *, priority_run_id: str | None = None, retry_run_id: str | None = None) -> dict[str, Any]:
        # Only explicit report-page POSTs call this method. No startup/workflow hooks.
        for selected in (priority_run_id, retry_run_id):
            if selected is not None:
                self.store.get_run(selected)
        candidates = sorted((run["run_id"] for run in self.store.list_runs()), key=str.casefold)
        for run_id in candidates:
            state = self.status(run_id)["status"]
            if state == "PENDING" or (state == "FAILED" and run_id == retry_run_id):
                self._errors.pop(run_id, None)
                self._pending.append(run_id)
        selected = priority_run_id or retry_run_id
        if selected in self._pending:
            self._pending.remove(selected)
            self._pending.insert(0, selected)
        if self._pending and (self._worker is None or self._worker.done()):
            self._worker = asyncio.create_task(self._drain(), name="report-translations")
        return self.snapshot()

    async def _drain(self) -> None:
        try:
            while self._pending:
                self._current = self._pending.pop(0)
                try:
                    await self._translate(self._current)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._errors[self._current] = "REPORT_TRANSLATION_FAILED"
                    logger.warning("Report translation failed (%s)", type(exc).__name__)
                finally:
                    self._current = None
        finally:
            self._worker = None

    def _retry_delay(self, attempt: int, failure: ContestLensError | None) -> float:
        delay = min(30.0, self.options.retry_backoff_seconds * 2 ** (attempt - 1))
        details = failure.details if failure and isinstance(failure.details, dict) else {}
        retry_after = details.get("retry_after_seconds")
        if isinstance(retry_after, (int, float)) and math.isfinite(retry_after) and retry_after >= 0:
            delay = max(delay, min(30.0, retry_after))
        return delay

    async def _wait_for_rate_limit(self) -> None:
        while True:
            remaining = self._rate_limit_until - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            await asyncio.sleep(remaining)

    async def _run_batches(
        self,
        batches: list[dict[str, str]],
        process: Callable[[dict[str, str]], Awaitable[None]],
    ) -> None:
        """Bound task creation as well as requests; always settle children before returning."""
        waiting = iter(enumerate(batches))
        active: dict[asyncio.Task, int] = {}
        failure: Exception | None = None

        def fill_slots() -> None:
            while len(active) < self.options.max_concurrency:
                item = next(waiting, None)
                if item is None:
                    break
                index, batch = item
                task = asyncio.create_task(process(batch), name=f"report-batch:{self._current}:{index}")
                active[task] = index

        try:
            fill_slots()
            while active:
                done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                for task in sorted(done, key=active.__getitem__):
                    active.pop(task)
                    try:
                        task.result()
                    except Exception as exc:
                        failure = failure or exc
                # On failure, keep already-issued batches so their successful work is saved,
                # but do not start more work until the user explicitly retries the report.
                if failure is None:
                    fill_slots()
            if failure is not None:
                raise failure
        finally:
            for task in active:
                task.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)

    async def _translate(self, run_id: str) -> None:
        source = self._source(run_id)
        if not source:
            return
        sections, source_hash = source
        texts = narrative_texts(sections)
        segments = {
            f"{key}:{offset // 2000}": value[offset:offset + 2000]
            for key, value in texts.items() for offset in range(0, len(value), 2000)
        }
        previous = self._cache(run_id, source_hash)
        saved_segments = previous.get("segments")
        if not isinstance(saved_segments, dict):
            saved_segments = {}
        completed = {key: value for key, value in saved_segments.items() if key in segments and isinstance(value, str) and value.strip()}
        cache = {
            "schema_version": CACHE_VERSION, "source_sha256": source_hash, "status": "TRANSLATING",
            "model": self.model.settings.model, "segments": completed, "total_segments": len(segments),
            "failed_segments": {},
            "translation_profile": {
                "reasoning_effort": self.options.reasoning_effort, "max_tokens": self.options.max_tokens,
                "timeout_seconds": self.options.timeout_seconds, "max_attempts": self.options.max_attempts,
                "batch_max_items": self.options.batch_max_items, "batch_max_chars": self.options.batch_max_chars,
                "max_concurrency": self.options.max_concurrency,
            },
        }

        def save() -> None:
            cache["updated_at"] = utc_now()
            atomic_write_json(self._path(run_id), cache)

        # Every batch merges into the same accumulator under one writer lock. No task
        # writes its own snapshot, so out-of-order responses cannot overwrite progress.
        writer_lock = asyncio.Lock()

        async def translate_batch(batch: dict[str, str]) -> None:
            unresolved = dict(batch)
            for attempt in range(1, self.options.max_attempts + 1):
                await self._wait_for_rate_limit()
                failure = None
                retryable = True
                rate_limited = False
                try:
                    translated = await self.model.translate_report_texts(unresolved)
                except ContestLensError as exc:
                    failure = exc
                    retryable = _retryable_translation_error(exc)
                    translated = {}
                    rate_limited = isinstance(exc.details, dict) and exc.details.get("http_status") == 429
                    if rate_limited:
                        self._rate_limit_until = max(
                            self._rate_limit_until,
                            asyncio.get_running_loop().time() + self._retry_delay(attempt, exc),
                        )
                accepted = {
                    key: value for key, value in translated.items()
                    if key in unresolved and isinstance(value, str) and value.strip()
                }
                async with writer_lock:
                    completed.update(accepted)
                    for key in accepted:
                        unresolved.pop(key)
                        cache["failed_segments"].pop(key, None)
                    for key in unresolved:
                        cache["failed_segments"][key] = {
                            "attempts": attempt,
                            "error_code": failure.code if failure else "REPORT_TRANSLATION_INCOMPLETE",
                        }
                    save()
                if not unresolved:
                    return
                if not retryable or attempt == self.options.max_attempts:
                    raise failure or ContestLensError(
                        "REPORT_TRANSLATION_INCOMPLETE", "Some report fragments remain untranslated",
                        {"missing_count": len(unresolved)}, status_code=502,
                    )
                if not rate_limited:
                    await asyncio.sleep(self._retry_delay(attempt, failure))
                # For 429, the shared gate at the next iteration supplies the delay.

        save()
        context = ModelRunContext(run_id, self._path(run_id).parent / "model_calls")
        token = current_model_run.set(context)
        try:
            remaining = deque((key, value) for key, value in segments.items() if key not in completed)
            batches = []
            while remaining:
                batch: dict[str, str] = {}
                chars = 0
                while remaining and len(batch) < self.options.batch_max_items and (not batch or chars + len(remaining[0][1]) <= self.options.batch_max_chars):
                    key, value = remaining.popleft()
                    batch[key] = value
                    chars += len(value)
                batches.append(batch)
            await self._run_batches(batches, translate_batch)
            cache["translations"] = {
                key: "".join(completed[f"{key}:{part}"] for part in range((len(value) + 1999) // 2000))
                for key, value in texts.items()
            }
            cache["status"] = "READY"
            cache["completed_at"] = utc_now()
            save()
        except asyncio.CancelledError:
            # Keep partial checkpoints; only the next explicit report visit resumes them.
            raise
        except Exception as exc:
            cache["status"] = "FAILED"
            cache["error_code"] = exc.code if isinstance(exc, ContestLensError) else "REPORT_TRANSLATION_FAILED"
            save()
        finally:
            current_model_run.reset(token)

    async def close(self) -> None:
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        self._pending.clear()
        self._rate_limit_until = 0.0

    async def forget_run(self, run_id: str) -> None:
        await self.forget_runs({run_id})

    async def forget_runs(self, run_ids: set[str]) -> None:
        """Stop report work before a run and its cache are permanently removed."""
        self._pending = [value for value in self._pending if value not in run_ids]
        for run_id in run_ids:
            self._errors.pop(run_id, None)
        if self._current in run_ids and self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
            self._current = None
            if self._pending:
                self._worker = asyncio.create_task(self._drain(), name="report-translations")
