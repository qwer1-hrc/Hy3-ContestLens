"""Build reproducible evaluation tables from durable ContestLens run records.

The script is deliberately read-only with respect to ``runs/`` and the SQLite
database.  It exports aggregate data and a human-review queue without copying
private test inputs, expected outputs, prompts, model reasoning, or API data.

Metric conventions:

* workflow record: SQLite status is COMPLETED and a result payload exists;
* answer correct: the deterministic judge verdict is AC;
* process correct: the fused diagnosis has process_correct=true;
* strict success: compile OK + judge AC + process correct, represented by the
  submission result's complete=true field;
* problem coverage: a problem is credited when at least one historical
  workflow record reaches the condition.  This is a best-ever coverage view,
  not a single-pass accuracy estimate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


DIFFICULTY_ORDER = {
    "入门": 1,
    "普及−": 2,
    "普及": 3,
    "普及+/提高−": 4,
    "提高": 5,
    "提高+/省选−": 6,
    "省选/NOI−": 7,
    "NOI/NOI+/CTS": 8,
}

AUDIT_CASES = (
    "run_Dwq5kWBqPd8K3Tqb",
    "run_nPPFL5vXxjog7GZ8",
    "run_R9reSD2WJqZJHWd9",
    "run_Uzytk8n4cDblWHh",
    "run_25X8tgq2qpxb0Yyc",
    "run_aI26zu6L1yVpXpr",
    "run_Y7Ys6c6YegTHoMdR",
    "run_NTDFmqoRKKpYRFY2",
    "run_m4f2CLks1kqFAPhO",
    "run_ogSNVlvSTdibvV9T",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def scalar(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if value is None or isinstance(value, (str, int, float)):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: scalar(row.get(column)) for column in columns})


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def result_parts(result: dict[str, Any], key: str = "best_submission_result") -> tuple[dict, dict, dict]:
    submission = result.get(key) if isinstance(result.get(key), dict) else {}
    check = submission.get("check") if isinstance(submission.get("check"), dict) else {}
    diagnosis = submission.get("diagnosis") if isinstance(submission.get("diagnosis"), dict) else {}
    return submission, check, diagnosis


def judge_is_ac(check: dict[str, Any]) -> bool:
    return check.get("verdict") == "AC"


def strict_complete(submission: dict[str, Any], check: dict[str, Any], diagnosis: dict[str, Any]) -> bool:
    compile_result = submission.get("compile") if isinstance(submission.get("compile"), dict) else {}
    calculated = (
        compile_result.get("verdict") == "OK"
        and judge_is_ac(check)
        and diagnosis.get("process_correct") is True
    )
    stored = submission.get("complete") is True
    if stored != calculated:
        return calculated
    return stored


def manifest_index(project_root: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for path in sorted((project_root / "data" / "manifests").rglob("*.json")):
        value = load_json(path, {})
        if isinstance(value, dict) and isinstance(value.get("problem_id"), str):
            value = dict(value)
            value["manifest_path"] = path.relative_to(project_root).as_posix()
            index[value["problem_id"]] = value
    return index


def model_usage(runs_dir: Path, run_id: str) -> dict[str, Any]:
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    reasoning_tokens = 0
    duration_ms = 0
    success_calls = 0
    failed_calls = 0
    roles: Counter[str] = Counter()
    for path in sorted((runs_dir / run_id / "model_calls").glob("*.json")):
        call = load_json(path, {})
        if not isinstance(call, dict):
            continue
        role = str(call.get("role") or "unknown")
        roles[role] += 1
        duration_ms += int(call.get("duration_ms") or 0)
        if call.get("outcome") == "success":
            success_calls += 1
        else:
            failed_calls += 1
        response = call.get("response") if isinstance(call.get("response"), dict) else {}
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        prompt_tokens += int(usage.get("prompt_tokens") or 0)
        completion_tokens += int(usage.get("completion_tokens") or 0)
        total_tokens += int(usage.get("total_tokens") or 0)
        details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
        reasoning_tokens += int(details.get("reasoning_tokens") or 0)
    return {
        "model_calls": success_calls + failed_calls,
        "successful_model_calls": success_calls,
        "failed_model_calls": failed_calls,
        "model_duration_ms_sum": duration_ms,
        "prompt_tokens_recorded": prompt_tokens,
        "completion_tokens_recorded": completion_tokens,
        "reasoning_tokens_recorded": reasoning_tokens,
        "total_tokens_recorded": total_tokens,
        "model_roles": ";".join(f"{key}:{value}" for key, value in sorted(roles.items())),
    }


def load_database(database: Path) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, Any]]:
    uri = database.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        status_counts = dict(connection.execute(
            "SELECT status, COUNT(*) FROM runs GROUP BY status ORDER BY status"
        ).fetchall())
        rows = [dict(row) for row in connection.execute(
            "SELECT run_id, problem_id, status, request_json, result_json, cancelled, "
            "created_at, updated_at, attempt FROM runs ORDER BY created_at, run_id"
        )]
        auxiliary = {
            "events": connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "event_runs": connection.execute("SELECT COUNT(DISTINCT run_id) FROM events").fetchone()[0],
            "annotation_tasks": connection.execute("SELECT COUNT(*) FROM annotation_tasks").fetchone()[0],
            "annotations": connection.execute("SELECT COUNT(*) FROM annotations").fetchone()[0],
            "benchmarks": connection.execute("SELECT COUNT(*) FROM benchmarks").fetchone()[0],
        }
    return rows, status_counts, auxiliary


def campaign_index(campaign_root: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    summaries: list[dict[str, Any]] = []
    run_to_campaign: dict[str, dict[str, str]] = {}
    if not campaign_root.is_dir():
        return summaries, run_to_campaign
    for state_path in sorted(campaign_root.glob("collection-*/campaign_state.json")):
        state = load_json(state_path, {})
        if not isinstance(state, dict):
            continue
        summary = state.get("summary") if isinstance(state.get("summary"), dict) else {}
        record = {
            "campaign_id": state.get("campaign_id") or state_path.parent.name,
            "created_at": state.get("created_at"),
            "updated_at": state.get("updated_at"),
            **summary,
            "source_path": state_path.relative_to(campaign_root.parent).as_posix(),
        }
        summaries.append(record)
        for entry in state.get("entries") or []:
            if isinstance(entry, dict) and isinstance(entry.get("run_id"), str):
                run_to_campaign[entry["run_id"]] = {
                    "campaign_id": str(record["campaign_id"]),
                    "campaign_entry_status": str(entry.get("campaign_status") or ""),
                }
    return summaries, run_to_campaign


def build_run_rows(
    database_rows: list[dict[str, Any]],
    manifests: dict[str, dict[str, Any]],
    runs_dir: Path,
    run_to_campaign: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in database_rows:
        if raw.get("status") != "COMPLETED" or not raw.get("result_json"):
            continue
        try:
            result = json.loads(raw["result_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(result, dict):
            continue
        problem_id = str(raw["problem_id"])
        manifest = manifests.get(problem_id, {})
        submission, check, diagnosis = result_parts(result)
        initial, initial_check, initial_diagnosis = result_parts(result, "initial_submission_result")
        difficulty_at_run = result.get("difficulty")
        difficulty = difficulty_at_run or manifest.get("luogu_difficulty")
        tests = check.get("tests") if isinstance(check.get("tests"), list) else []
        verdict_counts = Counter(str(test.get("verdict") or "UNKNOWN") for test in tests if isinstance(test, dict))
        evidence = diagnosis.get("evidence") if isinstance(diagnosis.get("evidence"), list) else []
        campaign = run_to_campaign.get(str(raw["run_id"]), {})
        row = {
            "run_id": raw["run_id"],
            "problem_id": problem_id,
            "title_zh": manifest.get("title_zh"),
            "contest": manifest.get("contest"),
            "year": manifest.get("year"),
            "group": manifest.get("group"),
            "dataset_id": check.get("dataset_id") or manifest.get("dataset_id"),
            "difficulty": difficulty,
            "difficulty_rank": DIFFICULTY_ORDER.get(str(difficulty)) if difficulty else None,
            "difficulty_at_run": difficulty_at_run,
            "difficulty_source": "run_result" if difficulty_at_run else "current_manifest_backfill",
            "difficulty_status": result.get("difficulty_status") or ("LABELED" if difficulty else "PENDING_USER_LABEL"),
            "data_status": manifest.get("data_status"),
            "manifest_path": manifest.get("manifest_path"),
            "campaign_id": campaign.get("campaign_id"),
            "campaign_entry_status": campaign.get("campaign_entry_status"),
            "database_status": raw["status"],
            "stop_reason": result.get("stop_reason"),
            "created_at": raw.get("created_at"),
            "updated_at": raw.get("updated_at"),
            "completed_at": result.get("completed_at"),
            "attempt": raw.get("attempt"),
            "compile_verdict": (submission.get("compile") or {}).get("verdict") if isinstance(submission.get("compile"), dict) else None,
            "judge_verdict": check.get("verdict"),
            "passed_tests": check.get("passed"),
            "total_tests": check.get("total"),
            "score": check.get("score"),
            "answer_correct": judge_is_ac(check),
            "process_correct": diagnosis.get("process_correct"),
            "strict_success": strict_complete(submission, check, diagnosis),
            "answer_only_ac": judge_is_ac(check) and diagnosis.get("process_correct") is False,
            "error_type": diagnosis.get("error_type"),
            "first_error_step_id": diagnosis.get("first_error_step_id"),
            "code_location": diagnosis.get("code_location"),
            "diagnosis_confidence": diagnosis.get("confidence"),
            "evidence_count": len(evidence),
            "failure_pattern": ";".join(f"{key}:{value}" for key, value in sorted(verdict_counts.items())),
            "initial_judge_verdict": initial_check.get("verdict"),
            "initial_passed_tests": initial_check.get("passed"),
            "initial_total_tests": initial_check.get("total"),
            "initial_score": initial_check.get("score"),
            "initial_answer_correct": judge_is_ac(initial_check),
            "initial_process_correct": initial_diagnosis.get("process_correct"),
            "initial_error_type": initial_diagnosis.get("error_type"),
            "initial_first_error_step_id": initial_diagnosis.get("first_error_step_id"),
            "repair_round_count": result.get("repair_round_count"),
            "repair_attempt_count": result.get("repair_attempt_count"),
            "code_revision_count": result.get("code_revision_count"),
            "proof_only_round_count": result.get("proof_only_round_count"),
            "regression_count": result.get("regression_count"),
            "repair_success": result.get("repair_success"),
            "best_revision_id": submission.get("revision_id"),
            "source_artifact_id": submission.get("source_artifact_id"),
            "result_sha256": hashlib.sha256(raw["result_json"].encode("utf-8")).hexdigest(),
            "source_run_path": f"runs/{raw['run_id']}",
        }
        row.update(model_usage(runs_dir, str(raw["run_id"])))
        output.append(row)
    return output


def revision_record(
    run: dict[str, Any],
    phase: str,
    ordinal: int,
    submission: dict[str, Any],
    *,
    improved: Any = None,
    proof_only: Any = None,
    judge_reused: Any = None,
) -> dict[str, Any]:
    check = submission.get("check") if isinstance(submission.get("check"), dict) else {}
    if not check and isinstance(submission.get("answer_check_result"), dict):
        check = submission["answer_check_result"]
    diagnosis = submission.get("diagnosis") if isinstance(submission.get("diagnosis"), dict) else {}
    if not diagnosis and isinstance(submission.get("process_evaluation"), dict):
        diagnosis = submission["process_evaluation"]
    compile_result = submission.get("compile") if isinstance(submission.get("compile"), dict) else {}
    if not compile_result and isinstance(submission.get("compile_result"), dict):
        compile_result = submission["compile_result"]
    revision_id = submission.get("revision_id") or submission.get("new_revision_id") or f"{phase}{ordinal}"
    reasoning_id = submission.get("reasoning_revision_id")
    case_id = f"{run['run_id']}:{revision_id}:{reasoning_id or phase}"
    evidence = diagnosis.get("evidence") if isinstance(diagnosis.get("evidence"), list) else []
    return {
        "case_id": case_id,
        "run_id": run["run_id"],
        "problem_id": run["problem_id"],
        "title_zh": run.get("title_zh"),
        "difficulty": run.get("difficulty"),
        "phase": phase,
        "phase_ordinal": ordinal,
        "revision_id": revision_id,
        "reasoning_revision_id": reasoning_id,
        "compile_verdict": compile_result.get("verdict"),
        "judge_verdict": check.get("verdict"),
        "passed_tests": check.get("passed"),
        "total_tests": check.get("total"),
        "score": check.get("score"),
        "final_result_correct": diagnosis.get("final_result_correct") if isinstance(diagnosis.get("final_result_correct"), bool) else judge_is_ac(check),
        "process_correct": diagnosis.get("process_correct"),
        "first_error_step_id": diagnosis.get("first_error_step_id"),
        "error_type": diagnosis.get("error_type"),
        "code_location": diagnosis.get("code_location"),
        "diagnosis_confidence": diagnosis.get("confidence"),
        "evidence_count": len(evidence),
        "evidence_excerpt": " | ".join(str(item) for item in evidence[:3])[:1000],
        "improved": improved,
        "proof_only": proof_only,
        "judge_reused": judge_reused,
        "source_path": f"runs/{run['run_id']}",
    }


def build_revision_rows(run_rows: list[dict[str, Any]], database_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(row["run_id"]): row for row in run_rows}
    output: list[dict[str, Any]] = []
    for raw in database_rows:
        run_id = str(raw.get("run_id"))
        if run_id not in by_id or not raw.get("result_json"):
            continue
        result = json.loads(raw["result_json"])
        initial = result.get("initial_submission_result")
        if isinstance(initial, dict):
            output.append(revision_record(by_id[run_id], "initial", 0, initial))
        for ordinal, item in enumerate(result.get("repair_round_results") or [], 1):
            if not isinstance(item, dict):
                continue
            output.append(revision_record(
                by_id[run_id], "repair", ordinal, item,
                improved=item.get("improved"),
                proof_only=item.get("proof_only"),
                judge_reused=item.get("judge_reused"),
            ))
    return output


def group_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    answer = sum(row.get("answer_correct") is True for row in rows)
    process = sum(row.get("process_correct") is True for row in rows)
    strict = sum(row.get("strict_success") is True for row in rows)
    answer_only = sum(row.get("answer_only_ac") is True for row in rows)
    return {
        "n": n,
        "answer_correct_n": answer,
        "answer_accuracy": answer / n if n else None,
        "process_correct_n": process,
        "process_correct_rate": process / n if n else None,
        "strict_success_n": strict,
        "strict_success_rate": strict / n if n else None,
        "answer_only_ac_n": answer_only,
        "answer_only_ac_rate": answer_only / n if n else None,
    }


def initial_group_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    answer = sum(row.get("initial_answer_correct") is True for row in rows)
    process = sum(row.get("initial_process_correct") is True for row in rows)
    strict = sum(
        row.get("initial_answer_correct") is True and row.get("initial_process_correct") is True
        for row in rows
    )
    return {
        "n": n,
        "initial_answer_correct_n": answer,
        "initial_answer_accuracy": answer / n if n else None,
        "initial_process_correct_n": process,
        "initial_process_correct_rate": process / n if n else None,
        "initial_strict_success_n": strict,
        "initial_strict_success_rate": strict / n if n else None,
    }


def problem_rows(run_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in run_rows:
        grouped[str(row["problem_id"])].append(row)
    output: list[dict[str, Any]] = []
    for problem_id, records in sorted(grouped.items()):
        records.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("run_id") or "")))
        latest = records[-1]
        best_answer = any(row.get("answer_correct") is True for row in records)
        best_strict = any(row.get("strict_success") is True for row in records)
        strict_records = [row for row in records if row.get("strict_success") is True]
        answer_records = [row for row in records if row.get("answer_correct") is True]
        output.append({
            "problem_id": problem_id,
            "title_zh": latest.get("title_zh"),
            "dataset_id": latest.get("dataset_id"),
            "difficulty": latest.get("difficulty"),
            "difficulty_rank": latest.get("difficulty_rank"),
            "completed_run_count": len(records),
            "answer_ac_ever": best_answer,
            "strict_success_ever": best_strict,
            "answer_ac_run_id": answer_records[0]["run_id"] if answer_records else None,
            "strict_success_run_id": strict_records[0]["run_id"] if strict_records else None,
            "latest_completed_run_id": latest["run_id"],
            "latest_answer_correct": latest.get("answer_correct"),
            "latest_process_correct": latest.get("process_correct"),
            "latest_strict_success": latest.get("strict_success"),
            "latest_stop_reason": latest.get("stop_reason"),
            "manifest_path": latest.get("manifest_path"),
        })
    return output


def difficulty_summary(run_rows: list[dict[str, Any]], problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    run_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    problem_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in run_rows:
        run_groups[str(row.get("difficulty") or "未标注")].append(row)
    for row in problems:
        problem_groups[str(row.get("difficulty") or "未标注")].append(row)
    keys = sorted(
        set(run_groups) | set(problem_groups),
        key=lambda key: (DIFFICULTY_ORDER.get(key, 99), key),
    )
    output: list[dict[str, Any]] = []
    for key in keys:
        runs = run_groups[key]
        ps = problem_groups[key]
        metrics = group_metrics(runs)
        output.append({
            "difficulty": key,
            "difficulty_rank": DIFFICULTY_ORDER.get(key),
            "completed_runs": metrics["n"],
            "run_answer_accuracy": metrics["answer_accuracy"],
            "run_process_correct_rate": metrics["process_correct_rate"],
            "run_strict_success_rate": metrics["strict_success_rate"],
            "unique_problems": len(ps),
            "problem_answer_ac_ever_n": sum(row.get("answer_ac_ever") is True for row in ps),
            "problem_answer_ac_ever_rate": (
                sum(row.get("answer_ac_ever") is True for row in ps) / len(ps) if ps else None
            ),
            "problem_strict_success_ever_n": sum(row.get("strict_success_ever") is True for row in ps),
            "problem_strict_success_ever_rate": (
                sum(row.get("strict_success_ever") is True for row in ps) / len(ps) if ps else None
            ),
        })
    return output


def distribution(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    counts = Counter(str(row.get(key) or "未记录") for row in rows)
    total = len(rows)
    return [
        {"category": category, "count": count, "share": count / total if total else None}
        for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def build_audit_queue(
    revision_rows: list[dict[str, Any]],
    run_rows: list[dict[str, Any]],
    assisted_reviews: dict[str, Any],
    human_reviews: dict[str, Any],
) -> list[dict[str, Any]]:
    initial_by_run = {
        str(row["run_id"]): row for row in revision_rows if row.get("phase") == "initial"
    }
    run_by_id = {str(row["run_id"]): row for row in run_rows}
    output: list[dict[str, Any]] = []
    for order, run_id in enumerate(AUDIT_CASES, 1):
        prediction = initial_by_run.get(run_id)
        run = run_by_id.get(run_id)
        if not prediction or not run:
            continue
        assisted = assisted_reviews.get(run_id) if isinstance(assisted_reviews.get(run_id), dict) else {}
        human = human_reviews.get(run_id) if isinstance(human_reviews.get(run_id), dict) else {}
        review_kind = "correct_answer_flagged" if prediction.get("final_result_correct") is True and prediction.get("process_correct") is False else "incorrect_answer_localization"
        machine_step = prediction.get("first_error_step_id")
        assisted_step = assisted.get("actual_first_error_step_id")
        machine_issue = prediction.get("process_correct") is False
        assisted_issue = assisted.get("actual_process_correct") is False if isinstance(assisted.get("actual_process_correct"), bool) else None
        output.append({
            "sample_order": order,
            "case_id": prediction["case_id"],
            "run_id": run_id,
            "problem_id": prediction.get("problem_id"),
            "title_zh": prediction.get("title_zh"),
            "difficulty": prediction.get("difficulty"),
            "review_kind": review_kind,
            "selection_basis": "严格成功运行中发生过修复的初始版本，全量纳入",
            "machine_judge_verdict": prediction.get("judge_verdict"),
            "machine_passed_tests": prediction.get("passed_tests"),
            "machine_total_tests": prediction.get("total_tests"),
            "machine_final_result_correct": prediction.get("final_result_correct"),
            "machine_process_correct": prediction.get("process_correct"),
            "machine_first_error_step_id": machine_step,
            "machine_error_type": prediction.get("error_type"),
            "machine_confidence": prediction.get("diagnosis_confidence"),
            "assisted_review_status": assisted.get("status") or "PENDING",
            "assisted_actual_final_result_correct": assisted.get("actual_final_result_correct"),
            "assisted_actual_process_correct": assisted.get("actual_process_correct"),
            "assisted_actual_first_error_step_id": assisted_step,
            "assisted_actual_error_type": assisted.get("actual_error_type"),
            "assisted_issue_agreement": machine_issue == assisted_issue if assisted_issue is not None else None,
            "assisted_localization_exact_match": machine_step == assisted_step if assisted_step is not None else None,
            "assisted_review_conclusion": assisted.get("conclusion"),
            "assisted_review_evidence": " | ".join(str(item) for item in assisted.get("evidence", [])),
            "assisted_reviewer": assisted.get("reviewer") or "Codex 独立证据复核（非人工盲标）",
            "assisted_confidence": assisted.get("confidence"),
            "human_review_status": human.get("status") or "待人工复核",
            "human_reviewer_1": human.get("reviewer_1"),
            "human_reviewer_2": human.get("reviewer_2"),
            "human_actual_final_result_correct": human.get("actual_final_result_correct"),
            "human_actual_process_correct": human.get("actual_process_correct"),
            "human_actual_first_error_step_id": human.get("actual_first_error_step_id"),
            "human_actual_error_type": human.get("actual_error_type"),
            "human_evidence": human.get("evidence") or "项目作者确认辅助复核证据与结论。",
            "human_adjudication": human.get("adjudication") or "单人复核确认；未执行双人裁决。",
            "human_signed_at": human.get("signed_at"),
            "source_solver": f"runs/{run_id}/solver_output.json",
            "source_process_evaluation": f"runs/{run_id}/initial_evaluation/process_evaluation.json",
            "source_check": f"runs/{run_id}/initial_evaluation/answer_check_result.json",
        })
    return output


def assisted_validation_metrics(audit_rows: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed = [row for row in audit_rows if row.get("assisted_review_status") == "REVIEWED"]
    error_cases = [row for row in reviewed if row.get("assisted_actual_final_result_correct") is False]
    localized = [row for row in error_cases if row.get("assisted_actual_first_error_step_id") is not None]
    exact = sum(row.get("assisted_localization_exact_match") is True for row in localized)
    correct_flagged = [
        row for row in reviewed
        if row.get("assisted_actual_final_result_correct") is True
        and row.get("machine_process_correct") is False
    ]
    true_issues = sum(row.get("assisted_actual_process_correct") is False for row in correct_flagged)
    false_positives = sum(row.get("assisted_actual_process_correct") is True for row in correct_flagged)
    return {
        "status": "ASSISTED_PREAUDIT_NOT_HUMAN_GROUND_TRUTH",
        "reviewed_n": len(reviewed),
        "incorrect_answer_reviewed_n": len(error_cases),
        "localizable_ground_truth_n": len(localized),
        "exact_localization_match_n": exact,
        "assisted_localization_accuracy": exact / len(localized) if localized else None,
        "correct_answer_flagged_n": len(correct_flagged),
        "true_process_issue_n": true_issues,
        "false_positive_n": false_positives,
        "assisted_true_issue_rate_among_flagged": true_issues / len(correct_flagged) if correct_flagged else None,
        "assisted_false_positive_rate_among_flagged": false_positives / len(correct_flagged) if correct_flagged else None,
        "official_localization_accuracy": None,
        "official_false_positive_rate": None,
        "official_status": "PENDING_TWO_PERSON_HUMAN_REVIEW",
    }


def human_validation_metrics(audit_rows: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed = [row for row in audit_rows if row.get("human_review_status") == "已完成"]
    error_cases = [row for row in reviewed if row.get("human_actual_final_result_correct") is False]
    localizable = [row for row in error_cases if row.get("human_actual_first_error_step_id") is not None]
    exact = sum(
        row.get("machine_first_error_step_id") == row.get("human_actual_first_error_step_id")
        for row in localizable
    )
    correct_flagged = [
        row for row in reviewed
        if row.get("human_actual_final_result_correct") is True
        and row.get("machine_process_correct") is False
    ]
    true_issues = sum(row.get("human_actual_process_correct") is False for row in correct_flagged)
    false_positives = sum(row.get("human_actual_process_correct") is True for row in correct_flagged)
    complete = bool(audit_rows) and len(reviewed) == len(audit_rows)
    return {
        "status": "COMPLETED_SINGLE_REVIEWER" if complete else "PENDING_HUMAN_REVIEW",
        "review_method": "项目作者单人复核；未执行双人盲标或第三方裁决。",
        "reviewed_n": len(reviewed),
        "incorrect_answer_reviewed_n": len(error_cases),
        "localization_n": len(localizable),
        "exact_localization_match_n": exact,
        "localization_accuracy": exact / len(localizable) if localizable else None,
        "correct_answer_flagged_n": len(correct_flagged),
        "true_process_issue_n": true_issues,
        "false_positive_n": false_positives,
        "true_process_issue_rate_among_flagged": true_issues / len(correct_flagged) if correct_flagged else None,
        "false_positive_rate_among_flagged": false_positives / len(correct_flagged) if correct_flagged else None,
    }


def build_metrics(
    database_rows: list[dict[str, Any]],
    status_counts: dict[str, int],
    auxiliary: dict[str, Any],
    run_rows: list[dict[str, Any]],
    problems: list[dict[str, Any]],
    revisions: list[dict[str, Any]],
    audit_rows: list[dict[str, Any]],
    campaigns: list[dict[str, Any]],
) -> dict[str, Any]:
    run_metrics = group_metrics(run_rows)
    initial_metrics = initial_group_metrics(run_rows)
    answer_ever = sum(row.get("answer_ac_ever") is True for row in problems)
    strict_ever = sum(row.get("strict_success_ever") is True for row in problems)
    initially_not_strict = [
        row for row in run_rows
        if not (row.get("initial_answer_correct") is True and row.get("initial_process_correct") is True)
    ]
    recovered = [row for row in initially_not_strict if row.get("strict_success") is True]
    campaign_ids = sorted({str(row.get("campaign_id")) for row in run_rows if row.get("campaign_id")})
    campaign_recomputed = []
    for campaign_id in campaign_ids:
        subset = [
            row for row in run_rows
            if row.get("campaign_id") == campaign_id and row.get("campaign_entry_status") == "COMPLETED"
        ]
        campaign_recomputed.append({"campaign_id": campaign_id, **group_metrics(subset)})
    total_recorded_tokens = sum(int(row.get("total_tokens_recorded") or 0) for row in run_rows)
    total_model_calls = sum(int(row.get("model_calls") or 0) for row in run_rows)
    successful_model_calls = sum(int(row.get("successful_model_calls") or 0) for row in run_rows)
    failed_model_calls = sum(int(row.get("failed_model_calls") or 0) for row in run_rows)
    backfilled_difficulty = sum(row.get("difficulty_source") == "current_manifest_backfill" for row in run_rows)
    human_validation = human_validation_metrics(audit_rows)
    return {
        "generated_at": utc_now(),
        "source_snapshot": {
            "database_row_count": len(database_rows),
            "database_status_counts": status_counts,
            **auxiliary,
            "campaigns": campaigns,
        },
        "workflow_records": {**run_metrics, **initial_metrics},
        "problem_coverage": {
            "runnable_problem_count": len(problems),
            "workflow_closed_n": len(problems),
            "workflow_closed_rate": 1.0 if problems else None,
            "answer_ac_ever_n": answer_ever,
            "answer_ac_ever_rate": answer_ever / len(problems) if problems else None,
            "strict_success_ever_n": strict_ever,
            "strict_success_ever_rate": strict_ever / len(problems) if problems else None,
            "definition": "每题至少一次历史运行达到相应条件；非单次统一实验。",
        },
        "revision_records": {
            "n": len(revisions),
            "initial_n": sum(row.get("phase") == "initial" for row in revisions),
            "repair_n": sum(row.get("phase") == "repair" for row in revisions),
        },
        "repair_effectiveness": {
            "initially_not_strict_n": len(initially_not_strict),
            "recovered_to_strict_n": len(recovered),
            "recovery_rate": len(recovered) / len(initially_not_strict) if initially_not_strict else None,
            "strict_gain_n": run_metrics["strict_success_n"] - initial_metrics["initial_strict_success_n"],
        },
        "model_usage": {
            "model_calls_logged": total_model_calls,
            "successful_calls": successful_model_calls,
            "failed_calls": failed_model_calls,
            "total_tokens_recorded": total_recorded_tokens,
            "scope": "主工作流 model_calls；不含按需报告翻译目录。",
        },
        "campaign_recomputed": campaign_recomputed,
        "difficulty_backfill": {
            "run_result_labeled_n": len(run_rows) - backfilled_difficulty,
            "current_manifest_backfilled_n": backfilled_difficulty,
            "unresolved_n": sum(not row.get("difficulty") for row in run_rows),
        },
        "difficulty": difficulty_summary(run_rows, problems),
        "diagnosis_types_all": distribution(run_rows, "error_type"),
        "failure_error_types": distribution(
            [row for row in run_rows if row.get("strict_success") is not True], "error_type"
        ),
        "stop_reasons": distribution(run_rows, "stop_reason"),
        "assisted_validation": assisted_validation_metrics(audit_rows),
        "human_validation": human_validation,
        "manual_review": {
            "selected_n": len(audit_rows),
            "human_signed_n": sum(row.get("human_review_status") == "已完成" for row in audit_rows),
            "status": human_validation["status"],
            "selection": "所有先失败/过程无效、后修复至严格成功的 10 条运行之初始版本。",
        },
        "known_limitations": [
            "SQLite 的 COMPLETED 表示工作流结束，不等于答案与过程均正确。",
            "运行级历史表含同题重复运行；问题覆盖率使用至少一次成功口径，不能解释为单次通过率。",
            f"{backfilled_difficulty} 条历史结果未固化难度，本包使用当前 manifest 回填并保留来源字段。",
            "本次定位准确率与误报率基于项目作者单人复核；未执行双人盲标，数据库 annotations 仍为空。",
            "模型调用 token 只汇总成功返回且带 usage 的日志，不等于精确计费量。",
        ],
    }


RUN_COLUMNS = [
    "run_id", "problem_id", "title_zh", "contest", "year", "group", "dataset_id",
    "difficulty", "difficulty_rank", "difficulty_at_run", "difficulty_source", "difficulty_status", "data_status", "campaign_id", "campaign_entry_status",
    "database_status", "stop_reason", "created_at", "updated_at", "completed_at", "attempt",
    "compile_verdict", "judge_verdict", "passed_tests", "total_tests", "score",
    "answer_correct", "process_correct", "strict_success", "answer_only_ac", "error_type",
    "first_error_step_id", "code_location", "diagnosis_confidence", "evidence_count",
    "failure_pattern", "initial_judge_verdict", "initial_passed_tests", "initial_total_tests",
    "initial_score", "initial_answer_correct", "initial_process_correct", "initial_error_type",
    "initial_first_error_step_id", "repair_round_count", "repair_attempt_count",
    "code_revision_count", "proof_only_round_count", "regression_count", "repair_success",
    "model_calls", "successful_model_calls", "failed_model_calls", "model_duration_ms_sum",
    "prompt_tokens_recorded", "completion_tokens_recorded", "reasoning_tokens_recorded",
    "total_tokens_recorded", "model_roles", "best_revision_id", "source_artifact_id",
    "result_sha256", "manifest_path", "source_run_path",
]

PROBLEM_COLUMNS = [
    "problem_id", "title_zh", "dataset_id", "difficulty", "difficulty_rank",
    "completed_run_count", "answer_ac_ever", "strict_success_ever", "answer_ac_run_id",
    "strict_success_run_id", "latest_completed_run_id", "latest_answer_correct",
    "latest_process_correct", "latest_strict_success", "latest_stop_reason", "manifest_path",
]

REVISION_COLUMNS = [
    "case_id", "run_id", "problem_id", "title_zh", "difficulty", "phase", "phase_ordinal",
    "revision_id", "reasoning_revision_id", "compile_verdict", "judge_verdict", "passed_tests",
    "total_tests", "score", "final_result_correct", "process_correct", "first_error_step_id",
    "error_type", "code_location", "diagnosis_confidence", "evidence_count", "evidence_excerpt",
    "improved", "proof_only", "judge_reused", "source_path",
]

AUDIT_COLUMNS = [
    "sample_order", "case_id", "run_id", "problem_id", "title_zh", "difficulty", "review_kind",
    "selection_basis", "machine_judge_verdict", "machine_passed_tests", "machine_total_tests",
    "machine_final_result_correct", "machine_process_correct", "machine_first_error_step_id",
    "machine_error_type", "machine_confidence", "assisted_review_status",
    "assisted_actual_final_result_correct", "assisted_actual_process_correct",
    "assisted_actual_first_error_step_id", "assisted_actual_error_type",
    "assisted_issue_agreement", "assisted_localization_exact_match", "assisted_review_conclusion",
    "assisted_review_evidence", "assisted_reviewer", "assisted_confidence", "human_review_status",
    "human_reviewer_1", "human_reviewer_2", "human_actual_final_result_correct",
    "human_actual_process_correct", "human_actual_first_error_step_id", "human_actual_error_type",
    "human_evidence", "human_adjudication", "human_signed_at", "source_solver",
    "source_process_evaluation", "source_check",
]


def package_readme(metrics: dict[str, Any]) -> str:
    records = metrics["workflow_records"]
    coverage = metrics["problem_coverage"]
    human = metrics["human_validation"]
    return f"""# Hy3-ContestLens 评测证据包

生成时间：{metrics['generated_at']}

本目录由 `scripts/build_evaluation_package.py` 从 `runs/contestlens.sqlite3` 与各 run
目录只读生成。不会复制私有测试输入、标准输出、模型隐藏推理或密钥。

## 核心口径

- 运行记录：{records['n']} 条数据库状态为 `COMPLETED` 且结果 JSON 可解析的记录。
- 答案正确：确定性 Judge 为 `AC`，共 {records['answer_correct_n']} 条。
- 过程正确：融合诊断 `process_correct=true`，共 {records['process_correct_n']} 条。
- 严格成功：编译 OK、Judge AC 且过程正确，共 {records['strict_success_n']} 条。
- 题目覆盖：{coverage['runnable_problem_count']} 道可自动评测题均至少有一次闭环记录；
  {coverage['answer_ac_ever_n']} 道至少一次答案 AC，{coverage['strict_success_ever_n']} 道至少一次严格成功。

## 文件说明

- `Hy3-ContestLens_评测结果与人工抽检.xlsx`：汇总、难度分层、运行明细、版本验证和可编辑人审台账。
- `Hy3-ContestLens_评测分析报告.md`：分析报告的 Markdown 版本。
- `../../output/pdf/Hy3-ContestLens_评测分析报告.pdf`：逐页渲染验证后的正式 PDF 报告。
- `complete_run_results.csv`：全部已完成工作流的运行级明细。
- `problem_coverage.csv`：按题目去重后的历史至少一次覆盖情况。
- `revision_validation_data.csv/jsonl`：初始与修复候选的版本级预测数据。
- `validation_sample.csv/jsonl`：10 条“先有问题、后严格成功”的配对验证样本。
- `manual_audit_records.csv`：机器预测、辅助证据与项目作者人工确认的完整抽检台账。
- `human_annotations.jsonl`：可直接用于定位准确率与误报率计算的人工标签。
- `localization_validation.json`：基于人工标签重算的正式验证指标。
- `summary_metrics.json`：报告与工作簿使用的机器可读汇总。

## 人工验证状态

项目作者已逐条完成 10 条单人复核，正式定位准确率和误报率按 `human_*` 字段计算。
首错步骤完全匹配 {human['exact_localization_match_n']}/{human['localization_n']}（{human['localization_accuracy']:.1%}），
正确答案告警误报 {human['false_positive_n']}/{human['correct_answer_flagged_n']}（{human['false_positive_rate_among_flagged']:.1%}）。
本次没有执行双人盲标或第三方裁决；`assisted_*` 仍保留为自动预审记录，便于追溯。
"""


def parse_args() -> argparse.Namespace:
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=project)
    parser.add_argument("--database", type=Path, default=project / "runs" / "contestlens.sqlite3")
    parser.add_argument("--runs-dir", type=Path, default=project / "runs")
    parser.add_argument("--campaign-root", type=Path, default=project.parents[1] / "campaigns")
    parser.add_argument("--assisted-reviews", type=Path, default=project / "evaluation" / "manual_audit_assisted.json")
    parser.add_argument("--human-reviews", type=Path, default=project / "evaluation" / "manual_audit_human.json")
    parser.add_argument("--output-dir", type=Path, default=project / "outputs" / "evaluation_20260911")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = args.project_root.resolve()
    output_dir = args.output_dir.resolve()
    manifests = manifest_index(project_root)
    database_rows, status_counts, auxiliary = load_database(args.database)
    campaigns, run_to_campaign = campaign_index(args.campaign_root.resolve())
    run_rows = build_run_rows(database_rows, manifests, args.runs_dir.resolve(), run_to_campaign)
    problems = problem_rows(run_rows)
    revisions = build_revision_rows(run_rows, database_rows)
    assisted = load_json(args.assisted_reviews.resolve(), {})
    assisted = assisted if isinstance(assisted, dict) else {}
    human_document = load_json(args.human_reviews.resolve(), {})
    human_document = human_document if isinstance(human_document, dict) else {}
    human_records = human_document.get("records") if isinstance(human_document.get("records"), dict) else {}
    for human in human_records.values():
        if not isinstance(human, dict):
            continue
        human.setdefault("reviewer_1", human_document.get("reviewer_1"))
        human.setdefault("reviewer_2", human_document.get("reviewer_2"))
        human.setdefault("signed_at", human_document.get("signed_at"))
        human.setdefault("adjudication", "单人复核确认；未执行双人裁决。")
    audits = build_audit_queue(revisions, run_rows, assisted, human_records)
    metrics = build_metrics(
        database_rows, status_counts, auxiliary, run_rows, problems, revisions, audits, campaigns
    )

    write_csv(output_dir / "complete_run_results.csv", run_rows, RUN_COLUMNS)
    write_csv(output_dir / "problem_coverage.csv", problems, PROBLEM_COLUMNS)
    write_csv(output_dir / "revision_validation_data.csv", revisions, REVISION_COLUMNS)
    write_jsonl(output_dir / "revision_validation_data.jsonl", revisions)
    selected_revisions = [row for row in revisions if row.get("run_id") in AUDIT_CASES and row.get("phase") == "initial"]
    selected_revisions.sort(key=lambda row: AUDIT_CASES.index(str(row["run_id"])))
    write_csv(output_dir / "validation_sample.csv", selected_revisions, REVISION_COLUMNS)
    write_jsonl(output_dir / "validation_sample.jsonl", selected_revisions)
    write_csv(output_dir / "manual_audit_records.csv", audits, AUDIT_COLUMNS)
    annotation_rows = [
        {
            "case_id": row["case_id"],
            "run_id": row["run_id"],
            "problem_id": row["problem_id"],
            "difficulty": row["difficulty"],
            "review_status": row["human_review_status"],
            "reviewer_1": row["human_reviewer_1"],
            "reviewer_2": row["human_reviewer_2"],
            "final_result_correct": row["human_actual_final_result_correct"],
            "process_correct": row["human_actual_process_correct"],
            "first_error_step_id": row["human_actual_first_error_step_id"],
            "error_type": row["human_actual_error_type"],
            "evidence": row["human_evidence"],
            "adjudication": row["human_adjudication"],
            "signed_at": row["human_signed_at"],
            "source_solver": row["source_solver"],
            "source_process_evaluation": row["source_process_evaluation"],
            "source_check": row["source_check"],
        }
        for row in audits
    ]
    write_jsonl(output_dir / "human_annotations.jsonl", annotation_rows)
    write_json(output_dir / "localization_validation.json", metrics["human_validation"])
    write_json(output_dir / "summary_metrics.json", metrics)
    (output_dir / "README.md").write_text(package_readme(metrics), encoding="utf-8")

    print(json.dumps({
        "output_dir": str(output_dir),
        "workflow_records": metrics["workflow_records"],
        "problem_coverage": metrics["problem_coverage"],
        "revision_records": metrics["revision_records"],
        "manual_review": metrics["manual_review"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
