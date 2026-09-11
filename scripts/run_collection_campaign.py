"""Run every still-unfinished ContestLens problem as a durable campaign.

The runner deliberately talks only to the public local REST API.  It does not
inspect private test data or source artifacts.  At campaign creation it reads
the local run index only to avoid re-running a problem that has already
finished a workflow successfully.

Policy encoded here:

* problems whose manifest has ``judge_note`` are recorded as excluded rather
  than being sent to a judge that is known to be unsuitable;
* ``省选/NOI−`` and ``NOI/NOI+/CTS`` problems are treated as complex and get
  five repair rounds; every other runnable problem gets three;
* a workflow failure is restarted at most once.  A mid-workflow failure is
  resumed through the same run ID; an already-finalized infrastructure failure
  gets one fresh run because a checkpointed final result cannot be recomputed
  in place.

The campaign state, CSV, JSON summary, and Markdown summary are updated after
every meaningful state transition.  If this process or the API service is
restarted, pass ``--resume <campaign-dir>`` to continue without duplicating
runs (per-run Idempotency-Key values are deterministic).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
COMPLEX_PREFIXES = ("省选/NOI", "NOI/")
STATE_VERSION = 1


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def campaign_id_now() -> str:
    return "collection-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def is_complex(difficulty: str | None) -> bool:
    return bool(difficulty and difficulty.strip().startswith(COMPLEX_PREFIXES))


def atomic_json(path: Path, value: Any) -> None:
    """Write a complete UTF-8 JSON file or leave the previous one intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def previous_run_index(database_path: Path) -> tuple[set[str], dict[str, str], set[str]]:
    """Return problem IDs with a complete workflow and currently active IDs.

    A complete result is stricter than status alone: old development fixtures
    or a partially migrated record are not silently accepted as a completed
    workflow.
    """
    if not database_path.is_file():
        raise RuntimeError(f"Run database does not exist: {database_path}")
    uri = database_path.resolve().as_uri() + "?mode=ro"
    # A running service may briefly checkpoint its WAL while this controller is
    # starting.  Retry that read-only snapshot without treating it as a
    # workflow restart or a controller failure.
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(1, 9):
        try:
            with sqlite3.connect(uri, uri=True, timeout=10) as connection:
                completed: set[str] = set()
                strict_success: set[str] = set()
                for problem_id, result_json in connection.execute(
                    "SELECT problem_id, result_json FROM runs "
                    "WHERE status='COMPLETED' AND result_json IS NOT NULL"
                ):
                    completed.add(problem_id)
                    try:
                        result = json.loads(result_json)
                    except (TypeError, ValueError):
                        continue
                    final = result.get("final_submission_result")
                    if isinstance(final, dict) and final.get("complete") is True:
                        strict_success.add(problem_id)
                # Only a run with a live lease counts as external work.  Old
                # CREATED/INTERRUPTED records are unfinished history, not a
                # reason to suppress a fresh campaign attempt.
                active: dict[str, str] = {}
                now = utc_now()
                for problem_id, status in connection.execute(
                    "SELECT problem_id, status FROM runs "
                    "WHERE cancelled=0 AND result_json IS NULL "
                    "AND status NOT IN ('COMPLETED','FAILED','CANCELLED','CREATED','INTERRUPTED') "
                    "AND runner_id IS NOT NULL AND lease_expires_at > ? "
                    "ORDER BY updated_at DESC",
                    (now,),
                ):
                    active.setdefault(problem_id, status)
            return completed, active, strict_success
        except sqlite3.OperationalError as exc:
            last_error = exc
            if attempt == 8:
                break
            time.sleep(min(10, attempt))
    raise RuntimeError(f"Could not read run database after WAL retries: {last_error}")


def safe_api_error(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict):
            detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
            code = body.get("error_code") or detail.get("error_code")
            message = body.get("message") or detail.get("message") or body.get("detail")
            return ": ".join(str(item) for item in (code, message) if item)[:500]
    except (ValueError, TypeError, AttributeError):
        pass
    return response.text.replace("\n", " ")[:500]


class ServiceGuardian:
    """Keep the local API/WebUI available after a process crash.

    The workflow service owns durable checkpoints, so restarting the Uvicorn
    process is safe: its startup supervisor claims recoverable runs from the
    same SQLite database.  The guardian never starts a second copy while the
    health endpoint is already responding.
    """

    def __init__(self, base_url: str, log_dir: Path):
        parsed = urlparse(base_url)
        self.base_url = base_url.rstrip("/")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.project_root = Path(__file__).resolve().parents[1]
        self.log_dir = log_dir
        self.lock = asyncio.Lock()
        self.restart_count = 0
        self._log_handles: list[Any] = []

    async def _healthy(self, client: httpx.AsyncClient) -> bool:
        try:
            response = await client.get("/healthz", timeout=5)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def _spawn(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stdout = (self.log_dir / "service-restart.stdout.log").open("a", encoding="utf-8")
        stderr = (self.log_dir / "service-restart.stderr.log").open("a", encoding="utf-8")
        self._log_handles.extend([stdout, stderr])
        env = os.environ.copy()
        source_root = str(self.project_root / "src")
        env["PYTHONPATH"] = source_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        command = [
            sys.executable, "-m", "uvicorn", "hy3_contestlens.api.app:app",
            "--host", self.host, "--port", str(self.port),
        ]
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(
            command, cwd=self.project_root, env=env,
            stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
            creationflags=flags, close_fds=(os.name != "nt"),
        )
        self.restart_count += 1

    async def ensure(self, client: httpx.AsyncClient) -> None:
        if await self._healthy(client):
            return
        async with self.lock:
            if await self._healthy(client):
                return
            self._spawn()
            for _ in range(60):
                if await self._healthy(client):
                    return
                await asyncio.sleep(2)
        raise RuntimeError("Local WebUI/API did not recover within 120 seconds")


async def api_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    attempts: int = 5,
    service: ServiceGuardian | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Retry transport/server failures and recover the local API if needed.

    HTTP 4xx responses are configuration or contract errors and are returned
    immediately; they are never confused with a workflow restart.
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = await client.request(method, path, json=payload, headers=headers)
            if response.status_code >= 500:
                detail = safe_api_error(response)
                last_error = RuntimeError(f"{method} {path} -> HTTP {response.status_code}{(': ' + detail) if detail else ''}")
                if service is not None:
                    try:
                        await service.ensure(client)
                    except Exception as recovery_error:
                        last_error = recovery_error
                if attempt < attempts:
                    await asyncio.sleep(min(20, 2 ** (attempt - 1)))
                    continue
                break
            if response.is_error:
                detail = safe_api_error(response)
                raise RuntimeError(f"{method} {path} -> HTTP {response.status_code}{(': ' + detail) if detail else ''}")
            value = response.json()
            if not isinstance(value, (dict, list)):
                raise RuntimeError(f"{method} {path} returned an unexpected JSON value")
            return value
        except httpx.HTTPError as exc:
            last_error = exc
            if service is not None:
                try:
                    await service.ensure(client)
                except Exception as recovery_error:
                    last_error = recovery_error
            if attempt == attempts:
                break
            await asyncio.sleep(min(20, 2 ** (attempt - 1)))
    raise RuntimeError(f"API request exhausted {attempts} controller attempts: {last_error}")


def result_metrics(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    if "error_code" in result:
        return {
            "workflow_error_code": result.get("error_code"),
            "workflow_error_message": result.get("message"),
        }
    best = result.get("best_submission_result")
    if not isinstance(best, dict):
        return {"stop_reason": result.get("stop_reason")}
    check = best.get("check") if isinstance(best.get("check"), dict) else {}
    diagnosis = best.get("diagnosis") if isinstance(best.get("diagnosis"), dict) else {}
    initial = result.get("initial_submission_result")
    initial_check = initial.get("check") if isinstance(initial, dict) else None
    initial_check = initial_check if isinstance(initial_check, dict) else {}
    return {
        "stop_reason": result.get("stop_reason"),
        "initial_passed": initial_check.get("passed"),
        "initial_total": initial_check.get("total"),
        "best_passed": check.get("passed"),
        "best_total": check.get("total"),
        "best_verdict": check.get("verdict"),
        "process_correct": diagnosis.get("process_correct"),
        "error_type": diagnosis.get("error_type"),
        "repair_round_count": result.get("repair_round_count"),
        "repair_attempt_count": result.get("repair_attempt_count"),
        "regression_count": result.get("regression_count"),
    }


class Campaign:
    def __init__(self, root: Path, state: dict[str, Any], *, poll_seconds: float, concurrency: int):
        self.root = root
        self.state_path = root / "campaign_state.json"
        self.state = state
        self.poll_seconds = poll_seconds
        self.concurrency = concurrency
        self.lock = asyncio.Lock()

    @property
    def entries(self) -> list[dict[str, Any]]:
        return self.state["entries"]

    async def save(self) -> None:
        async with self.lock:
            self._refresh_outputs()

    def _refresh_outputs(self) -> None:
        self.state["updated_at"] = utc_now()
        self.state["summary"] = build_summary(self.state)
        atomic_json(self.state_path, self.state)
        atomic_json(self.root / "summary.json", self.state["summary"])
        write_csv(self.root / "results.csv", self.entries)
        write_markdown(self.root / "SUMMARY.md", self.state)

    async def update(self, entry: dict[str, Any], **changes: Any) -> None:
        async with self.lock:
            entry.update(changes)
            entry["updated_at"] = utc_now()
            self._refresh_outputs()


def build_summary(state: dict[str, Any]) -> dict[str, Any]:
    entries = state["entries"]
    statuses = Counter(item["campaign_status"] for item in entries)
    complexity = Counter(item["complexity_class"] for item in entries)
    pending_complexity = Counter(
        item["complexity_class"] for item in entries if item["campaign_status"] == "PENDING"
    )
    run_entries = [item for item in entries if item.get("run_id")]
    all_done = all(
        item["campaign_status"]
        in {
            "SKIPPED_PREVIOUS_SUCCESS",
            "EXCLUDED_UNSUPPORTED_COMPARATOR",
            "EXCLUDED_SAMPLES_ONLY",
            "EXCLUDED_SAMPLES_AFTER_RUN",
            "COMPLETED",
            "FAILED_AFTER_RESTART",
            "CANCELLED",
            "CONTROLLER_ERROR",
        }
        for item in entries
    )
    finished = [item for item in entries if item["campaign_status"] == "COMPLETED"]
    return {
        "catalog_problem_count": len(entries),
        "campaign_id": state["campaign_id"],
        "campaign_complete": all_done,
        "counts_by_campaign_status": dict(sorted(statuses.items())),
        "counts_by_complexity_class": dict(sorted(complexity.items())),
        "pending_counts_by_complexity_class": dict(sorted(pending_complexity.items())),
        "previous_successes_skipped": statuses["SKIPPED_PREVIOUS_SUCCESS"],
        "historical_strict_successes_skipped": sum(
            bool(item.get("historical_strict_success"))
            for item in entries if item["campaign_status"] == "SKIPPED_PREVIOUS_SUCCESS"
        ),
        "unsupported_comparator_excluded": statuses["EXCLUDED_UNSUPPORTED_COMPARATOR"],
        "samples_only_excluded": statuses["EXCLUDED_SAMPLES_ONLY"] + statuses["EXCLUDED_SAMPLES_AFTER_RUN"],
        "campaign_runs_started": len(run_entries),
        "campaign_workflows_completed": len(finished),
        "campaign_workflows_failed_after_restart": statuses["FAILED_AFTER_RESTART"],
        "campaign_workflows_cancelled": statuses["CANCELLED"],
        "controller_errors": statuses["CONTROLLER_ERROR"],
        "workflow_restarts_used": sum(int(item.get("workflow_restarts_used", 0)) for item in entries),
        "fully_passing_runs": sum(
            item.get("metrics", {}).get("best_passed") == item.get("metrics", {}).get("best_total")
            and item.get("metrics", {}).get("best_total") not in (None, 0)
            for item in finished
        ),
        "generated_at": utc_now(),
    }


def write_csv(path: Path, entries: list[dict[str, Any]]) -> None:
    columns = [
        "problem_id", "title_zh", "dataset_id", "luogu_difficulty", "complexity_class",
        "repair_max_rounds", "campaign_status", "run_id", "workflow_restarts_used",
        "previous_run_ids", "judge_note", "started_at", "finished_at", "updated_at",
        "stop_reason", "initial_passed", "initial_total", "best_passed", "best_total",
        "best_verdict", "process_correct", "error_type", "repair_round_count",
        "repair_attempt_count", "regression_count", "workflow_error_code", "controller_error",
    ]
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for entry in entries:
            metrics = entry.get("metrics") if isinstance(entry.get("metrics"), dict) else {}
            writer.writerow({
                **entry,
                **metrics,
                "previous_run_ids": ";".join(entry.get("previous_run_ids", [])),
            })
    os.replace(temporary, path)


def write_markdown(path: Path, state: dict[str, Any]) -> None:
    summary = state["summary"]
    lines = [
        "# 全题集自动运行汇总",
        "",
        f"- 活动 ID：`{state['campaign_id']}`",
        f"- 创建时间：{state['created_at']}",
        f"- 最近更新：{state['updated_at']}",
        f"- 是否结束：{'是' if summary['campaign_complete'] else '否'}",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
    ]
    fields = [
        ("题库总题数", "catalog_problem_count"),
        ("跳过的历史成功题", "previous_successes_skipped"),
        ("其中严格全通过题", "historical_strict_successes_skipped"),
        ("未接入专用比较器而排除", "unsupported_comparator_excluded"),
        ("仅样例数据而排除", "samples_only_excluded"),
        ("本次已创建运行", "campaign_runs_started"),
        ("本次工作流完成", "campaign_workflows_completed"),
        ("重启后仍失败", "campaign_workflows_failed_after_restart"),
        ("本次完全通过", "fully_passing_runs"),
        ("已使用的工作流重启", "workflow_restarts_used"),
        ("控制器错误", "controller_errors"),
    ]
    lines.extend(f"| {label} | {summary[key]} |" for label, key in fields)
    lines.extend([
        "",
        "复杂题口径：洛谷难度为 `省选/NOI−` 或 `NOI/NOI+/CTS`；该类最多 5 轮修复，其余可自动评测题最多 3 轮。",
        "历史中只要已有 `COMPLETED` 且已保存结果的题目即不再运行。工作流中途失败至多重启一次。",
        "",
        "详细逐题结果见 `results.csv` 和 `campaign_state.json`。",
    ])
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def initial_state(
    *,
    problems: list[dict[str, Any]],
    completed: set[str],
    active: dict[str, str],
    strict_success: set[str],
    base_url: str,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for problem in sorted(problems, key=lambda row: (not is_complex(row.get("luogu_difficulty")), row["problem_id"])):
        difficulty = problem.get("luogu_difficulty")
        complex_problem = is_complex(difficulty)
        entry: dict[str, Any] = {
            "problem_id": problem["problem_id"],
            "title_zh": problem.get("title_zh"),
            "dataset_id": problem.get("dataset_id"),
            "luogu_difficulty": difficulty,
            "complexity_class": "complex" if complex_problem else "simple",
            "repair_max_rounds": 5 if complex_problem else 3,
            "judge_note": problem.get("judge_note"),
            "data_status": problem.get("data_status"),
            "sample_only": problem.get("data_status") == "samples",
            "previous_run_ids": [],
            "workflow_restarts_used": 0,
            "activation": 0,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        if problem.get("data_status") == "samples":
            entry["campaign_status"] = "EXCLUDED_SAMPLES_ONLY"
        elif problem.get("judge_note"):
            entry["campaign_status"] = "EXCLUDED_UNSUPPORTED_COMPARATOR"
        elif problem["problem_id"] in completed:
            entry["campaign_status"] = "SKIPPED_PREVIOUS_SUCCESS"
            entry["historical_strict_success"] = problem["problem_id"] in strict_success
        elif problem["problem_id"] in active:
            # Do not start a duplicate beside somebody else's existing workflow.
            entry["campaign_status"] = "EXTERNAL_IN_PROGRESS"
            entry["external_status"] = active[problem["problem_id"]]
        else:
            entry["campaign_status"] = "PENDING"
        entries.append(entry)
    return {
        "schema_version": STATE_VERSION,
        "campaign_id": campaign_id_now(),
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "options": {
            "base_url": base_url,
            "simple_repair_max_rounds": 3,
            "complex_repair_max_rounds": 5,
            "complex_difficulty_prefixes": list(COMPLEX_PREFIXES),
            "workflow_restart_limit": 1,
            "image_understanding": "skip",
            "historical_success_rule": "status=COMPLETED and result_json is present",
        },
        "entries": entries,
    }


async def start_fresh_run(
    campaign: Campaign,
    client: httpx.AsyncClient,
    entry: dict[str, Any],
    service: ServiceGuardian | None = None,
) -> None:
    campaign_id = campaign.state["campaign_id"]
    key = f"{campaign_id}:{entry['problem_id']}:activation:{entry['activation']}"
    payload = {
        "problem_id": entry["problem_id"],
        "repair": {"enabled": True, "max_rounds": entry["repair_max_rounds"]},
        "image_understanding": "skip",
    }
    created = await api_request(
        client, "POST", "/api/v1/runs", payload=payload,
        headers={"Idempotency-Key": key}, service=service,
    )
    if not isinstance(created, dict) or not isinstance(created.get("run_id"), str):
        raise RuntimeError("Run creation did not return a run_id")
    await campaign.update(entry, run_id=created["run_id"], campaign_status="CREATED", started_at=utc_now())
    await api_request(client, "POST", f"/api/v1/runs/{created['run_id']}/start", service=service)
    await campaign.update(entry, campaign_status="RUNNING")


async def restart_once(
    campaign: Campaign,
    client: httpx.AsyncClient,
    entry: dict[str, Any],
    run: dict[str, Any],
    service: ServiceGuardian | None = None,
) -> None:
    """Use exactly one restart token, resuming only when that can do useful work."""
    restart_number = int(entry.get("workflow_restarts_used", 0)) + 1
    history = list(entry.get("restart_history", []))
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    if "error_code" in result:
        history.append({
            "at": utc_now(), "mode": "resume_same_run", "run_id": entry["run_id"],
            "error_code": result.get("error_code"),
        })
        await campaign.update(
            entry,
            campaign_status="RESTARTING",
            workflow_restarts_used=restart_number,
            restart_history=history,
        )
        await api_request(client, "POST", f"/api/v1/runs/{entry['run_id']}/start", service=service)
        await campaign.update(entry, campaign_status="RUNNING")
        return

    # A finalized non-COMPLETED result is already present in its checkpoint.  The
    # service correctly restores that immutable result on /start, so a fresh
    # attempt is the only meaningful "restart" in this case.
    history.append({
        "at": utc_now(), "mode": "fresh_run_after_finalized_failure", "run_id": entry["run_id"],
        "stop_reason": result.get("stop_reason"),
    })
    old_run_id = entry["run_id"]
    await campaign.update(
        entry,
        campaign_status="RESTARTING",
        workflow_restarts_used=restart_number,
        previous_run_ids=[*entry.get("previous_run_ids", []), old_run_id],
        restart_history=history,
        activation=int(entry.get("activation", 0)) + 1,
        run_id=None,
    )
    await start_fresh_run(campaign, client, entry, service)


async def work_entry(
    campaign: Campaign,
    client: httpx.AsyncClient,
    entry: dict[str, Any],
    service: ServiceGuardian | None = None,
) -> None:
    try:
        if entry["campaign_status"] == "EXTERNAL_IN_PROGRESS":
            # Existing work is deliberately not manipulated.  A future resume
            # can reconsider this record after that independently-owned run ends.
            return
        if entry["campaign_status"] in {
            "SKIPPED_PREVIOUS_SUCCESS", "EXCLUDED_UNSUPPORTED_COMPARATOR", "COMPLETED",
            "EXCLUDED_SAMPLES_ONLY", "EXCLUDED_SAMPLES_AFTER_RUN",
            "FAILED_AFTER_RESTART", "CANCELLED", "CONTROLLER_ERROR",
        }:
            return
        if not entry.get("run_id"):
            await start_fresh_run(campaign, client, entry, service)
        while True:
            run_id = entry.get("run_id")
            if not run_id:
                await start_fresh_run(campaign, client, entry, service)
                run_id = entry["run_id"]
            run = await api_request(client, "GET", f"/api/v1/runs/{run_id}", service=service)
            if not isinstance(run, dict):
                raise RuntimeError(f"Run {run_id} state has an unexpected shape")
            status = run.get("status")
            if status not in TERMINAL:
                if status == "CREATED":
                    # The controller may have been interrupted after durable
                    # creation but before the start request.  Finish that
                    # idempotent transition on resume instead of polling a
                    # run the service will never claim.
                    await api_request(client, "POST", f"/api/v1/runs/{run_id}/start", service=service)
                    await campaign.update(entry, campaign_status="RUNNING", server_status="QUEUED")
                    continue
                if entry.get("campaign_status") != "RUNNING" or entry.get("server_status") != status:
                    await campaign.update(entry, campaign_status="RUNNING", server_status=status)
                await asyncio.sleep(campaign.poll_seconds)
                continue
            metrics = result_metrics(run.get("result"))
            if status == "COMPLETED":
                await campaign.update(
                    entry,
                    campaign_status="COMPLETED",
                    server_status=status,
                    metrics=metrics,
                    finished_at=utc_now(),
                )
                return
            if status == "CANCELLED":
                await campaign.update(
                    entry,
                    campaign_status="CANCELLED",
                    server_status=status,
                    metrics=metrics,
                    finished_at=utc_now(),
                )
                return
            if int(entry.get("workflow_restarts_used", 0)) < 1:
                await restart_once(campaign, client, entry, run, service)
                continue
            await campaign.update(
                entry,
                campaign_status="FAILED_AFTER_RESTART",
                server_status=status,
                metrics=metrics,
                finished_at=utc_now(),
            )
            return
    except Exception as exc:
        await campaign.update(
            entry,
            campaign_status="CONTROLLER_ERROR",
            controller_error=f"{type(exc).__name__}: {str(exc)[:700]}",
            finished_at=utc_now(),
        )


async def enforce_samples_only(
    campaign: Campaign,
    problems: list[dict[str, Any]],
    client: httpx.AsyncClient,
    service: ServiceGuardian | None = None,
) -> None:
    """Migrate older campaign state and cancel any accidentally-started sample run."""
    by_id = {item.get("problem_id"): item for item in problems}
    for entry in campaign.entries:
        problem = by_id.get(entry.get("problem_id"))
        if not problem:
            continue
        sample_only = problem.get("data_status") == "samples"
        await campaign.update(
            entry,
            data_status=problem.get("data_status"),
            sample_only=sample_only,
        )
        if not sample_only:
            continue
        if entry.get("campaign_status") in {
            "EXCLUDED_SAMPLES_ONLY", "EXCLUDED_SAMPLES_AFTER_RUN",
        }:
            if entry.get("run_id"):
                try:
                    fetched = await api_request(
                        client, "GET", f"/api/v1/runs/{entry['run_id']}", service=service,
                    )
                    if isinstance(fetched, dict):
                        await campaign.update(
                            entry,
                            server_status=fetched.get("status"),
                            metrics=result_metrics(fetched.get("result")) or entry.get("metrics", {}),
                        )
                except Exception:
                    pass
            continue
        old_status = entry.get("campaign_status")
        run_id = entry.get("run_id")
        run: dict[str, Any] | None = None
        if run_id:
            try:
                fetched = await api_request(client, "GET", f"/api/v1/runs/{run_id}", service=service)
                run = fetched if isinstance(fetched, dict) else None
                if run and run.get("status") not in TERMINAL:
                    await api_request(client, "POST", f"/api/v1/runs/{run_id}/cancel", service=service)
                    fetched = await api_request(client, "GET", f"/api/v1/runs/{run_id}", service=service)
                    run = fetched if isinstance(fetched, dict) else run
            except Exception as exc:
                # The run remains visible in the campaign record.  If the API
                # is temporarily down, the guardian/retry path will try again
                # on the next resume rather than silently claiming cancellation.
                await campaign.update(entry, controller_error=f"sample-policy: {type(exc).__name__}: {str(exc)[:300]}")
        after_run = bool(run_id) or old_status in {
            "CREATED", "RUNNING", "RESTARTING", "COMPLETED", "FAILED", "CANCELLED",
        }
        await campaign.update(
            entry,
            campaign_status="EXCLUDED_SAMPLES_AFTER_RUN" if after_run else "EXCLUDED_SAMPLES_ONLY",
            metrics=result_metrics(run.get("result")) if run else entry.get("metrics", {}),
            finished_at=utc_now(),
        )


async def run_campaign(campaign: Campaign, service: ServiceGuardian | None = None) -> None:
    base_url = str(campaign.state["options"]["base_url"]).rstrip("/")
    service = service or ServiceGuardian(base_url, campaign.root)
    timeout = httpx.Timeout(connect=15, read=60, write=60, pool=60)
    limits = httpx.Limits(max_connections=max(4, campaign.concurrency * 2), max_keepalive_connections=campaign.concurrency)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, limits=limits) as client:
        semaphore = asyncio.Semaphore(campaign.concurrency)

        async def limited(entry: dict[str, Any]) -> None:
            async with semaphore:
                await work_entry(campaign, client, entry, service)

        candidates = [
            entry for entry in campaign.entries
            if entry["campaign_status"] not in {
                "SKIPPED_PREVIOUS_SUCCESS", "EXCLUDED_UNSUPPORTED_COMPARATOR",
                "EXCLUDED_SAMPLES_ONLY", "EXCLUDED_SAMPLES_AFTER_RUN", "COMPLETED",
                "FAILED_AFTER_RESTART", "CANCELLED", "CONTROLLER_ERROR", "EXTERNAL_IN_PROGRESS",
            }
        ]
        await asyncio.gather(*(limited(entry) for entry in candidates))
    await campaign.save()


async def load_problems(base_url: str, service: ServiceGuardian | None = None) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=httpx.Timeout(30)) as client:
        data = await api_request(client, "GET", "/api/v1/problems", service=service)
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise RuntimeError("Problem catalog returned an unexpected shape")
    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8200")
    parser.add_argument("--database", type=Path, default=Path("runs/contestlens.sqlite3"))
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument("--resume", type=Path, help="Existing campaign directory to resume")
    parser.add_argument("--concurrency", type=int, default=2, help="Concurrent workflows (default: 2)")
    parser.add_argument("--poll-seconds", type=float, default=10, help="Run-status polling interval (default: 10)")
    parser.add_argument("--dry-run", action="store_true", help="Print selection counts without creating any run")
    args = parser.parse_args()
    if args.concurrency < 1 or args.concurrency > 4:
        parser.error("--concurrency must be between 1 and 4")
    if args.poll_seconds < 1:
        parser.error("--poll-seconds must be at least 1")
    if args.resume and args.dry_run:
        parser.error("--resume and --dry-run cannot be combined")
    return args


async def main_async(args: argparse.Namespace) -> int:
    if args.resume:
        root = args.resume.resolve()
        state_path = root / "campaign_state.json"
        if not state_path.is_file():
            raise RuntimeError(f"Campaign state was not found: {state_path}")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("schema_version") != STATE_VERSION:
            raise RuntimeError("Unsupported campaign state version")
        # A resume intentionally preserves the original target service and
        # policies; only scheduling controls are allowed to vary.
        campaign = Campaign(root, state, poll_seconds=args.poll_seconds, concurrency=args.concurrency)
        service = ServiceGuardian(str(state["options"]["base_url"]), root)
        problems = await load_problems(str(state["options"]["base_url"]), service)
        async with httpx.AsyncClient(
            base_url=str(state["options"]["base_url"]).rstrip("/"),
            timeout=httpx.Timeout(connect=15, read=60, write=60, pool=60),
        ) as client:
            await enforce_samples_only(campaign, problems, client, service)
        await campaign.save()
        print(f"Resuming {state['campaign_id']} from {root}", flush=True)
        await run_campaign(campaign, service)
        print(json.dumps(campaign.state["summary"], ensure_ascii=False, indent=2), flush=True)
        return 0

    completed, active, strict_success = previous_run_index(args.database)
    problems = await load_problems(args.base_url)
    state = initial_state(
        problems=problems, completed=completed, active=active,
        strict_success=strict_success, base_url=args.base_url,
    )
    summary = build_summary(state)
    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    root = (args.reports_dir / state["campaign_id"]).resolve()
    if root.exists():
        raise RuntimeError(f"Campaign path already exists: {root}")
    campaign = Campaign(root, state, poll_seconds=args.poll_seconds, concurrency=args.concurrency)
    await campaign.save()
    print(f"Started {state['campaign_id']} in {root}", flush=True)
    await run_campaign(campaign)
    print(json.dumps(campaign.state["summary"], ensure_ascii=False, indent=2), flush=True)
    return 0


def main() -> None:
    args = parse_args()
    try:
        raise SystemExit(asyncio.run(main_async(args)))
    except KeyboardInterrupt:
        print("Campaign controller interrupted. Resume with --resume <campaign-dir>.", file=sys.stderr)
        raise SystemExit(130) from None
    except Exception as exc:
        print(f"Campaign controller failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
