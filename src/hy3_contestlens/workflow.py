from __future__ import annotations

import asyncio
import difflib
import json
from pathlib import Path
from typing import Any

from .domain import CheckResult, CompileResult, CriticReview, Diagnosis, ErrorType, RunStatus, SolverOutput, Verdict
from .errors import ContestLensError
from .evaluation import adjudicate, code_review_conflicts_with_compile, improvement_key, is_complete
from .judge import DockerJudge
from .model import Hy3Client
from .resources import ResourceService
from .store import Store
from .utils import atomic_write_json, safe_id, utc_now
from .workspace import WorkspaceStore


class ContestWorkflow:
    def __init__(self, store: Store, resources: ResourceService, workspace: WorkspaceStore, judge: DockerJudge, model: Hy3Client, manifests: Any):
        self.store = store
        self.resources = resources
        self.workspace = workspace
        self.judge = judge
        self.model = model
        self.manifests = manifests

    def _write(self, run_id: str, relative: str, data: Any) -> None:
        atomic_write_json(self.workspace.settings.runs_root / run_id / relative, data)

    def _status(self, run_id: str, status: RunStatus, **event: Any) -> None:
        self.store.update_run(run_id, status.value, event=event)

    def _cancelled(self, run_id: str) -> bool:
        return self.store.get_run(run_id)["cancelled"]

    async def _reviews(self, problem_spec: dict[str, Any], solution: SolverOutput, io_basename: str) -> tuple[CriticReview, CriticReview]:
        algorithm_task = self.model.algorithm_review(problem_spec, solution)
        code_task = self.model.code_review(problem_spec, solution, io_basename)
        return await asyncio.gather(algorithm_task, code_task)

    async def _recheck_code_review_if_conflicting(
        self,
        problem_spec: dict[str, Any],
        solution: SolverOutput,
        io_basename: str,
        code_review: CriticReview,
        compile_result: CompileResult,
    ) -> tuple[CriticReview, CriticReview | None]:
        if not code_review_conflicts_with_compile(code_review, compile_result.verdict):
            return code_review, None
        rechecked = await self.model.code_review(
            problem_spec,
            solution,
            io_basename,
            compile_evidence=compile_result.model_dump(mode="json"),
        )
        return rechecked, code_review

    def _judge_revision(self, run_id: str, problem_id: str, submission_id: str, revision_id: str, sha256: str) -> tuple[CompileResult, CheckResult | None, dict[str, Any]]:
        frozen = self.workspace.freeze_cpp_revision(run_id, submission_id, revision_id, sha256)
        compile_result = self.judge.compile_cpp(problem_id, frozen["source_artifact_id"], frozen["source_sha256"])
        check = None
        if compile_result.verdict == Verdict.OK and compile_result.compile_artifact_id:
            check = self.judge.check_answer(compile_result.compile_artifact_id, "noip2018", problem_id)
        return compile_result, check, frozen

    async def execute(self, run_id: str) -> None:
        try:
            await self._execute(run_id)
        except ContestLensError as exc:
            result = {"error_code": exc.code, "message": exc.message, "details": exc.details, "completed_at": utc_now()}
            self._write(run_id, "failure.json", result)
            self.store.update_run(run_id, RunStatus.FAILED.value, result=result, event={"error_code": exc.code, "message": exc.message})
        except Exception as exc:
            result = {"error_code": "INTERNAL_ERROR", "message": "Workflow failed", "details": {"type": type(exc).__name__, "reason": str(exc)}, "completed_at": utc_now()}
            self._write(run_id, "failure.json", result)
            self.store.update_run(run_id, RunStatus.FAILED.value, result=result, event={"error_code": "INTERNAL_ERROR"})

    async def _execute(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        problem_id = run["problem_id"]
        request = run["request"]
        binding = self.store.get_binding(problem_id)
        manifest = self.manifests.get(problem_id)
        if self._cancelled(run_id):
            return
        self._status(run_id, RunStatus.ANALYZING)
        document = self.resources.read_problem_document(binding["scope_id"], binding["document"])
        while document.get("next_cursor") is not None:
            following = self.resources.read_problem_document(binding["scope_id"], binding["document"], cursor=document["next_cursor"])
            document["content"] += following["content"]
            document["next_cursor"] = following["next_cursor"]
        public_problem = {
            "problem_id": problem_id, "title_zh": manifest.title_zh, "difficulty": manifest.luogu_difficulty,
            "time_ms": manifest.resource_limits.time_ms, "memory_mb": manifest.resource_limits.memory_mb,
            "io": manifest.io.model_dump(), "source": {"document_id": document["document_id"], "sha256": document["sha256"]},
        }
        problem_spec = await self.model.analyze_problem(document, public_problem)
        problem_spec["source"] = public_problem["source"]
        self._write(run_id, "problem_spec.json", problem_spec)
        if self._cancelled(run_id):
            return
        self._status(run_id, RunStatus.SOLVING)
        io_basename = manifest.io.basename
        solution = await self.model.solve(problem_spec, io_basename)
        self._write(run_id, "solver_output.json", solution.model_dump(mode="json"))
        submission = self.workspace.create_cpp_submission(run_id, problem_id, solution.cpp_source)
        submission_id = submission["submission_id"]
        revision_id = submission["revision_id"]
        revision_sha = submission["sha256"]
        if self._cancelled(run_id):
            return
        self._status(run_id, RunStatus.REVIEWING, revision_id=revision_id)
        algorithm_review, code_review = await self._reviews(problem_spec, solution, io_basename)
        self._status(run_id, RunStatus.COMPILING, revision_id=revision_id)
        compile_result, check, frozen = self._judge_revision(run_id, problem_id, submission_id, revision_id, revision_sha)
        if compile_result.verdict == Verdict.OK:
            self._status(run_id, RunStatus.JUDGING, revision_id=revision_id)
        code_review, initial_code_review = await self._recheck_code_review_if_conflicting(
            problem_spec, solution, io_basename, code_review, compile_result
        )
        self._write(run_id, "algorithm_critic.json", algorithm_review.model_dump(mode="json"))
        if initial_code_review is not None:
            self._write(run_id, "code_critic_initial.json", initial_code_review.model_dump(mode="json"))
            self._write(run_id, "code_critic_recheck.json", code_review.model_dump(mode="json"))
        self._write(run_id, "code_critic.json", code_review.model_dump(mode="json"))
        diagnosis = adjudicate(algorithm_review, code_review, compile_result.verdict, check)
        initial = self._evaluation_record(revision_id, revision_sha, compile_result, check, diagnosis, frozen)
        self._write(run_id, "initial_evaluation/compile_result.json", compile_result.model_dump(mode="json"))
        if check:
            self._write(run_id, "initial_evaluation/answer_check_result.json", check.model_dump(mode="json"))
        self._write(run_id, "initial_evaluation/process_evaluation.json", diagnosis.model_dump(mode="json"))
        best = initial
        best_solution = solution
        best_key = improvement_key(compile_result.verdict, check, diagnosis)
        rounds: list[dict[str, Any]] = []
        regression_count = 0
        no_improvement = 0
        requested_rounds = int(request.get("repair", {}).get("max_rounds", self.workspace.settings.repair_max_rounds))
        max_rounds = min(max(1, requested_rounds), self.workspace.settings.repair_hard_max_rounds)
        repair_enabled = bool(request.get("repair", {}).get("enabled", True))
        stop_reason = "COMPLETED" if is_complete(compile_result.verdict, check, diagnosis) else None
        infrastructure = compile_result.verdict == Verdict.SANDBOX_UNAVAILABLE or (check and check.verdict == Verdict.SANDBOX_UNAVAILABLE)
        if infrastructure:
            stop_reason = "INFRASTRUCTURE_ERROR"
        if diagnosis.error_type == ErrorType.UNRESOLVED and not diagnosis.final_result_correct:
            stop_reason = stop_reason or "UNRESOLVED"
        for round_number in range(1, max_rounds + 1):
            if stop_reason or not repair_enabled or self._cancelled(run_id):
                break
            self._status(run_id, RunStatus.REPAIRING, round=round_number, base_revision_id=best["revision_id"])
            repair_plan_id = safe_id("repairplan")
            repair_plan = {
                "repair_plan_id": repair_plan_id, "target_revision": best["revision_id"],
                "root_cause": best["diagnosis"]["evidence"], "first_error_step_id": best["diagnosis"]["first_error_step_id"],
                "error_type": best["diagnosis"]["error_type"], "repair_scope": best["diagnosis"]["repair_suggestion"],
                "required_changes": [best["diagnosis"]["repair_suggestion"]] if best["diagnosis"]["repair_suggestion"] else [],
                "regression_risks": ["Previously passing tests", "Complexity claim", "Code-step mapping"],
                "success_criteria": ["Compilation succeeds", "All formal tests pass", "No high-confidence contradicted process step"],
            }
            repaired = await self.model.repair(
                problem_spec,
                best_solution,
                best["diagnosis"],
                best.get("check") or {"verdict": best["compile"]["verdict"]},
                round_number,
                io_basename,
                compile_summary=best["compile"],
            )
            old_source = self.workspace.read_cpp_submission(run_id, submission_id, best["revision_id"])["source_code"]
            diff = "".join(difflib.unified_diff(old_source.splitlines(keepends=True), repaired.cpp_source.splitlines(keepends=True), fromfile="a/main.cpp", tofile="b/main.cpp"))
            if not diff:
                stop_reason = "STALLED"
                break
            revision = self.workspace.apply_cpp_patch(
                run_id, submission_id, best["revision_id"], best["sha256"], diff, round_number, repair_plan_id,
                repaired.steps[0].statement if repaired.steps else "Hy3 repair",
            )
            self._status(run_id, RunStatus.REJUDGING, round=round_number, revision_id=revision["revision_id"])
            compile_result, check, frozen = self._judge_revision(run_id, problem_id, submission_id, revision["revision_id"], revision["sha256"])
            algorithm_review, code_review = await self._reviews(problem_spec, repaired, io_basename)
            code_review, initial_code_review = await self._recheck_code_review_if_conflicting(
                problem_spec, repaired, io_basename, code_review, compile_result
            )
            diagnosis = adjudicate(algorithm_review, code_review, compile_result.verdict, check)
            current = self._evaluation_record(revision["revision_id"], revision["sha256"], compile_result, check, diagnosis, frozen)
            current_key = improvement_key(compile_result.verdict, check, diagnosis)
            improved = current_key > best_key
            if improved:
                best, best_solution, best_key = current, repaired, current_key
                no_improvement = 0
            else:
                no_improvement += 1
                if current_key < best_key:
                    regression_count += 1
            round_record = {
                "repair_round_id": f"round_{round_number:03d}", "parent_revision_id": repair_plan["target_revision"],
                "repair_plan": repair_plan, "new_revision_id": revision["revision_id"], "new_sha256": revision["sha256"],
                "compile_result": compile_result.model_dump(mode="json"), "answer_check_result": check.model_dump(mode="json") if check else None,
                "algorithm_review": algorithm_review.model_dump(mode="json"), "code_review": code_review.model_dump(mode="json"),
                "code_review_initial": initial_code_review.model_dump(mode="json") if initial_code_review else None,
                "code_review_recheck_triggered": initial_code_review is not None,
                "process_evaluation": diagnosis.model_dump(mode="json"), "improved": improved,
                "loop_decision": "COMPLETE" if is_complete(compile_result.verdict, check, diagnosis) else "CONTINUE",
            }
            rounds.append(round_record)
            self._write(run_id, f"repair_rounds/round_{round_number:03d}/round.json", round_record)
            if is_complete(compile_result.verdict, check, diagnosis):
                best, best_solution, best_key = current, repaired, current_key
                stop_reason = "COMPLETED"
            elif compile_result.verdict == Verdict.SANDBOX_UNAVAILABLE or (check and check.verdict == Verdict.SANDBOX_UNAVAILABLE):
                stop_reason = "INFRASTRUCTURE_ERROR"
            elif diagnosis.error_type == ErrorType.UNRESOLVED:
                stop_reason = "UNRESOLVED"
            elif no_improvement >= self.workspace.settings.stop_after_no_improvement_rounds:
                stop_reason = "STALLED"
        if self._cancelled(run_id):
            return
        stop_reason = stop_reason or ("MAX_ROUNDS" if repair_enabled else "REPAIR_DISABLED")
        final_result = {
            "run_id": run_id, "problem_id": problem_id, "resource_binding_id": binding["binding_id"],
            "submission_id": submission_id, "initial_submission_result": initial, "repair_round_results": rounds,
            "best_submission_result": best, "final_submission_result": best,
            "repair_success": not initial["complete"] and best["complete"], "repair_round_count": len(rounds),
            "regression_count": regression_count, "stop_reason": stop_reason,
            "difficulty": manifest.luogu_difficulty, "difficulty_status": "PENDING_USER_LABEL" if manifest.luogu_difficulty is None else "LABELED",
            "completed_at": utc_now(),
        }
        self._write(run_id, "best_revision.json", {"revision_id": best["revision_id"], "sha256": best["sha256"], "selection_key": list(best_key)})
        self._write(run_id, "final_evaluation.json", final_result)
        status = RunStatus.COMPLETED if stop_reason in {"COMPLETED", "MAX_ROUNDS", "STALLED", "UNRESOLVED", "REPAIR_DISABLED"} else RunStatus.FAILED
        self.store.update_run(run_id, status.value, result=final_result, event={"stop_reason": stop_reason, "best_revision_id": best["revision_id"]})

    @staticmethod
    def _evaluation_record(revision_id: str, sha256: str, compile_result: CompileResult, check: CheckResult | None, diagnosis: Diagnosis, frozen: dict[str, Any]) -> dict[str, Any]:
        return {
            "revision_id": revision_id, "sha256": sha256, "source_artifact_id": frozen["source_artifact_id"],
            "compile": compile_result.model_dump(mode="json"), "check": check.model_dump(mode="json") if check else None,
            "diagnosis": diagnosis.model_dump(mode="json"), "complete": is_complete(compile_result.verdict, check, diagnosis),
        }
