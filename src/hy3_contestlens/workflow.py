from __future__ import annotations

import asyncio
import difflib
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .domain import CheckResult, CompileResult, CriticReview, Diagnosis, ErrorType, RunStatus, SolverOutput, Verdict
from .errors import ContestLensError
from .evaluation import (
    adjudicate,
    code_review_conflicts_with_compile,
    critic_review_quality_issues,
    improvement_key,
    is_complete,
    repair_quality_gate,
)
from .judge import DockerJudge
from .image_understanding import ImageDescriptionResult, ImageUnderstandingClient, StatementImages, augment_document
from .model import Hy3Client
from .model_diagnostics import model_run_context
from .problem_spec import build_problem_spec
from .resources import ResourceService
from .store import Store
from .utils import atomic_write_json, safe_id, utc_now
from .workspace import WorkspaceStore


class ContestWorkflow:
    def __init__(self, store: Store, resources: ResourceService, workspace: WorkspaceStore, judge: DockerJudge, model: Hy3Client, manifests: Any, image_model: ImageUnderstandingClient | None = None):
        self.store = store
        self.resources = resources
        self.workspace = workspace
        self.judge = judge
        self.model = model
        self.manifests = manifests
        self.image_model = image_model

    def _write(self, run_id: str, relative: str, data: Any) -> None:
        atomic_write_json(self.workspace.settings.runs_root / run_id / relative, data)

    def _read_json(self, run_id: str, relative: str) -> dict[str, Any] | None:
        path = self.workspace.settings.runs_root / run_id / relative
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContestLensError(
                "RUN_CHECKPOINT_CORRUPT", "A persisted workflow artifact cannot be read",
                {"artifact": relative, "type": type(exc).__name__}, 409,
            ) from exc
        if not isinstance(value, dict):
            raise ContestLensError(
                "RUN_CHECKPOINT_CORRUPT", "A persisted workflow artifact has an invalid shape",
                {"artifact": relative}, 409,
            )
        return value

    def _load_checkpoint(self, run_id: str) -> dict[str, Any]:
        value = self._read_json(run_id, "workflow_checkpoint.json")
        if value is None:
            return {}
        if value.get("schema_version") != 1 or value.get("run_id") != run_id:
            raise ContestLensError(
                "RUN_CHECKPOINT_CORRUPT", "Workflow checkpoint identity is invalid", {"run_id": run_id}, 409,
            )
        return value

    def _save_checkpoint(self, run_id: str, checkpoint: dict[str, Any], stage: str) -> None:
        checkpoint.update(schema_version=1, run_id=run_id, stage=stage, updated_at=utc_now())
        self._write(run_id, "workflow_checkpoint.json", checkpoint)

    def _status(self, run_id: str, status: RunStatus, **event: Any) -> None:
        self.store.update_run(run_id, status.value, event=event)

    def _cancelled(self, run_id: str) -> bool:
        return self.store.get_run(run_id)["cancelled"]

    async def _reviews(self, problem_spec: dict[str, Any], solution: SolverOutput, io_basename: str) -> tuple[CriticReview, CriticReview]:
        algorithm_task = self.model.algorithm_review(problem_spec, solution)
        code_task = self.model.code_review(problem_spec, solution, io_basename)
        return await asyncio.gather(algorithm_task, code_task)

    async def _recheck_code_review_if_needed(
        self,
        problem_spec: dict[str, Any],
        solution: SolverOutput,
        io_basename: str,
        code_review: CriticReview,
        compile_result: CompileResult,
        check: CheckResult | None = None,
    ) -> tuple[CriticReview, CriticReview | None]:
        needs_runtime_evidence = (
            compile_result.verdict == Verdict.OK
            and check is not None
            and check.verdict != Verdict.AC
        )
        needs_compile_evidence = compile_result.verdict == Verdict.CE
        needs_quality_recheck = bool(critic_review_quality_issues(
            code_review, (step.step_id for step in solution.steps),
        ))
        if not (
            code_review_conflicts_with_compile(code_review, compile_result.verdict)
            or needs_runtime_evidence
            or needs_compile_evidence
            or needs_quality_recheck
        ):
            return code_review, None
        rechecked = await self.model.code_review(
            problem_spec,
            solution,
            io_basename,
            compile_evidence=compile_result.model_dump(mode="json"),
            judge_evidence=check.model_dump(mode="json") if check else None,
        )
        return rechecked, code_review

    async def _judge_revision(
        self,
        run_id: str,
        problem_id: str,
        submission_id: str,
        revision_id: str,
        sha256: str,
        *,
        phase: str,
        round_number: int | None = None,
    ) -> tuple[CompileResult, CheckResult | None, dict[str, Any]]:
        frozen = self.workspace.freeze_cpp_revision(run_id, submission_id, revision_id, sha256)
        compile_result = await asyncio.to_thread(
            self.judge.compile_cpp,
            problem_id,
            frozen["source_artifact_id"],
            frozen["source_sha256"],
        )
        event_context = {
            "phase": phase,
            "round": round_number,
            "revision_id": revision_id,
        }
        self.store.append_event(
            run_id,
            "COMPILE_COMPLETED",
            {**event_context, "compile": compile_result.model_dump(mode="json")},
        )
        check = None
        if compile_result.verdict == Verdict.OK and compile_result.compile_artifact_id:
            judge_status = RunStatus.JUDGING if phase == "initial" else RunStatus.REJUDGING
            self._status(run_id, judge_status, **event_context)
            check = await asyncio.to_thread(
                self.judge.check_answer,
                compile_result.compile_artifact_id,
                self.manifests.get(problem_id).dataset_id,
                problem_id,
            )
            self.store.append_event(
                run_id,
                "JUDGE_COMPLETED",
                {**event_context, "check": check.model_dump(mode="json")},
            )
        else:
            self.store.append_event(
                run_id,
                "JUDGE_SKIPPED",
                {
                    **event_context,
                    "reason": "compile_failed",
                    "compile_verdict": compile_result.verdict.value,
                },
            )
        return compile_result, check, frozen

    async def execute(self, run_id: str) -> None:
        parents: dict[str, int | None] = {}

        def progress(data: dict[str, Any]) -> None:
            call_id = data["call_id"]
            if call_id not in parents:
                status = self.store.get_run(run_id)["status"]
                parents[call_id] = next((event["seq"] for event in reversed(self.store.list_events(run_id))
                                         if event["type"] == status), None)
            self.store.append_event(run_id, "MODEL_CALL_PROGRESS", {**data, "parent_seq": parents[call_id]})

        try:
            with model_run_context(self.workspace.settings.runs_root, run_id, lambda: self._cancelled(run_id), progress):
                await self._execute(run_id)
        except ContestLensError as exc:
            if self._cancelled(run_id):
                return
            result = {"error_code": exc.code, "message": exc.message, "details": exc.details, "completed_at": utc_now()}
            self._write(run_id, "failure.json", result)
            self.store.update_run(run_id, RunStatus.FAILED.value, result=result, event={"error_code": exc.code, "message": exc.message})
        except Exception as exc:
            if self._cancelled(run_id):
                return
            result = {"error_code": "INTERNAL_ERROR", "message": "Workflow failed", "details": {"type": type(exc).__name__, "reason": str(exc)}, "completed_at": utc_now()}
            self._write(run_id, "failure.json", result)
            self.store.update_run(run_id, RunStatus.FAILED.value, result=result, event={"error_code": "INTERNAL_ERROR"})

    async def _prepare_images(self, run_id: str, binding: dict[str, Any], document: dict[str, Any], mode: str) -> dict[str, Any]:
        settings = self.workspace.settings.image_understanding
        images = StatementImages(self.resources, settings)
        state: dict[str, Any] = {"model": settings.model, "descriptions": [], "warnings": []}

        def save(status: str, **updates: Any) -> None:
            state.update(status=status, **updates)
            self.store.put_image_understanding(run_id, state)
            self._write(run_id, "image_understanding.json", state)

        def skipped(reason: str) -> dict[str, Any]:
            save("skipped", reason=reason)
            self.store.append_event(run_id, "IMAGE_UNDERSTANDING_SKIPPED", {"reason": reason, "warnings": state["warnings"]})
            return document

        try:
            inspection = await asyncio.to_thread(images.inspect, binding["scope_id"], binding["document"])
            state.update(inspection)
        except Exception as exc:
            # Inspection is optional, including on machines without rendering packages.
            state["warnings"].append({"error_code": exc.code if isinstance(exc, ContestLensError) else "IMAGE_INSPECTION_FAILED"})
            return skipped("inspection_failed")
        if self._cancelled(run_id):
            save("cancelled")
            return document
        if not state["images"]:
            return skipped("no_supported_images" if state["warnings"] else "no_images")
        if mode == "skip":
            return skipped("user_skipped")
        if not settings.configured:
            return skipped("invalid_configuration" if settings.configuration_error else "not_configured")
        missing_dependencies = images.missing_render_dependencies(binding["document"])
        if missing_dependencies:
            state["warnings"].append({"error_code": "IMAGE_DEPENDENCY_MISSING", "missing": missing_dependencies})
            return skipped("missing_dependencies")

        if mode == "ask":
            timeout = settings.decision_timeout_seconds
            request_id = safe_id("image_choice")
            save("awaiting_choice", request_id=request_id, choice=None,
                 expires_at=(datetime.now(timezone.utc) + timedelta(seconds=timeout)).isoformat())
            self._status(run_id, RunStatus.WAITING_FOR_IMAGE_CONFIRMATION,
                         request_id=request_id, model=settings.model,
                         images=[{"label": item["label"], "reasons": item["reasons"]} for item in state["images"]],
                         timeout_seconds=timeout)
            deadline = time.monotonic() + timeout
            while True:
                current = self.store.get_run(run_id)
                if current["cancelled"]:
                    save("cancelled")
                    return document
                decision = current["image_understanding"]
                if decision.get("choice"):
                    state.update(decision)
                    break
                if time.monotonic() >= deadline:
                    # Use the same atomic decision path as the UI; a simultaneous user click wins safely.
                    try:
                        self.store.decide_image_understanding(run_id, request_id, "skip")
                        state["timed_out"] = True
                    except ContestLensError:
                        pass
                    continue
                await asyncio.sleep(min(0.25, max(0, deadline - time.monotonic())))
            if state["choice"] == "skip":
                document = skipped("decision_timeout" if state.get("timed_out") else "user_skipped")
                self._status(run_id, RunStatus.ANALYZING)
                return document

        save("running")
        self._status(run_id, RunStatus.UNDERSTANDING_IMAGES, model=settings.model, image_count=len(state["images"]))
        client = self.image_model or ImageUnderstandingClient(settings)
        for item in state["images"]:
            if self._cancelled(run_id):
                save("cancelled")
                return document
            try:
                data_url = await asyncio.to_thread(images.render, binding["scope_id"], binding["document"], item)
                if self._cancelled(run_id):
                    save("cancelled")
                    return document
                description_result = await client.describe(data_url, item["label"], document["content"])
                if isinstance(description_result, ImageDescriptionResult):
                    description = description_result.text
                    diagnostics = description_result.diagnostics
                else:
                    # Preserve compatibility with simple custom/mock vision clients.
                    description = description_result
                    diagnostics = None
                if self._cancelled(run_id):
                    save("cancelled")
                    return document
                description_record = {
                    "image_id": item["image_id"], "source_label": item["label"], "model": settings.model,
                    "content_type": "untrusted_problem_content", "text": description,
                }
                if diagnostics:
                    description_record["diagnostics"] = diagnostics
                state["descriptions"].append(description_record)
                self.store.append_event(run_id, "IMAGE_DESCRIPTION_READY", {"label": item["label"], "text": description})
            except Exception as exc:
                warning = {"label": item["label"], "error_code": exc.code if isinstance(exc, ContestLensError) else "IMAGE_PROCESSING_FAILED"}
                if isinstance(exc, ContestLensError) and isinstance(exc.details, dict):
                    warning.update({key: exc.details[key] for key in (
                        "type", "duration_ms", "http_status", "request_id", "failure_kind", "stream",
                        "provider_error_type", "retry_after_seconds", "attempts", "retry_exhausted", "retry_history",
                    ) if key in exc.details})
                state["warnings"].append(warning)
                if warning.get("provider_error_type") in {"engine_overloaded_error", "rate_limit_reached_error"}:
                    remaining = state["images"][state["images"].index(item) + 1:]
                    state["warnings"].extend({
                        "label": remaining_item["label"], "error_code": "IMAGE_SKIPPED_AFTER_RATE_LIMIT",
                    } for remaining_item in remaining)
                    save("running")
                    break
            save("running")
        if self._cancelled(run_id):
            save("cancelled")
            return document
        outcome = "partial" if state["descriptions"] and state["warnings"] else "completed" if state["descriptions"] else "failed"
        save(outcome)
        self.store.append_event(run_id, "IMAGE_UNDERSTANDING_COMPLETED", {
            "status": outcome, "described": len(state["descriptions"]), "warnings": state["warnings"],
        })
        self._status(run_id, RunStatus.ANALYZING)
        return augment_document(document, state["descriptions"])

    async def _execute(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        problem_id = run["problem_id"]
        request = run["request"]
        binding = self.store.get_binding(problem_id)
        binding_id = binding.get("binding_id") or request.get("resource_binding_id") or "binding_unknown"
        manifest = self.manifests.get(problem_id)
        if self._cancelled(run_id):
            return

        checkpoint = self._load_checkpoint(run_id)
        public_problem = {
            "problem_id": problem_id,
            "title_zh": manifest.title_zh,
            "difficulty": manifest.luogu_difficulty,
            "time_ms": manifest.resource_limits.time_ms,
            "memory_mb": manifest.resource_limits.memory_mb,
            "io": manifest.io.model_dump(),
        }

        # Import durable artifacts written by builds that predate checkpoints.
        if not checkpoint:
            legacy_document = self._read_json(run_id, "problem_document.json")
            legacy_spec = self._read_json(run_id, "problem_spec.json")
            if legacy_document is not None and legacy_spec is not None:
                public_problem["source"] = legacy_spec.get("source", {
                    "document_id": legacy_document.get("document_id"),
                    "sha256": legacy_document.get("sha256"),
                })
                checkpoint.update(
                    binding_id=binding_id,
                    document=legacy_document,
                    public_problem=public_problem,
                    problem_spec=legacy_spec,
                )
                legacy_solution = self._read_json(run_id, "solver_output.json")
                if legacy_solution is not None:
                    solution = SolverOutput.model_validate(legacy_solution)
                    submission = self.workspace.find_cpp_submission(run_id, problem_id, solution.cpp_source)
                    if submission is not None:
                        checkpoint.update(solution=legacy_solution, submission=submission)
                self._save_checkpoint(run_id, checkpoint, "LEGACY_ARTIFACTS_IMPORTED")

        # The final result is checkpointed before the SQLite terminal transition.
        # A crash in that tiny window therefore needs no repeated work.
        if checkpoint.get("final_result") is not None:
            final_result = checkpoint["final_result"]
            final_status = RunStatus.COMPLETED if final_result.get("stop_reason") in {
                "COMPLETED", "MAX_ROUNDS", "STALLED", "UNRESOLVED", "REPAIR_DISABLED",
            } else RunStatus.FAILED
            self.store.update_run(
                run_id,
                final_status.value,
                result=final_result,
                event={"stop_reason": final_result.get("stop_reason"), "recovered_from_checkpoint": True},
            )
            self._save_checkpoint(run_id, checkpoint, "FINALIZED")
            return

        if checkpoint.get("problem_spec") is not None:
            document = checkpoint["document"]
            public_problem = checkpoint["public_problem"]
            problem_spec = checkpoint["problem_spec"]
            self.store.append_event(run_id, "RUN_RESUMED", {"checkpoint_stage": checkpoint.get("stage", "UNKNOWN")})
        else:
            self._status(run_id, RunStatus.ANALYZING)
            document = self.resources.read_problem_document(binding["scope_id"], binding["document"])
            while document.get("next_cursor") is not None:
                following = self.resources.read_problem_document(
                    binding["scope_id"], binding["document"], cursor=document["next_cursor"],
                )
                document["content"] += following["content"]
                document["next_cursor"] = following["next_cursor"]
            document = await self._prepare_images(
                run_id, binding, document, request.get("image_understanding", "skip"),
            )
            if self._cancelled(run_id):
                return
            self._write(run_id, "problem_document.json", document)
            public_problem["source"] = {
                "document_id": document["document_id"], "sha256": document["sha256"],
            }
            analysis = await self.model.analyze_problem(document, public_problem)
            self._write(run_id, "problem_analysis.json", analysis)
            problem_spec = build_problem_spec(analysis, document, public_problem)
            self._write(run_id, "problem_spec.json", problem_spec)
            checkpoint.update(
                binding_id=binding_id, document=document,
                public_problem=public_problem, problem_spec=problem_spec,
            )
            self._save_checkpoint(run_id, checkpoint, "PROBLEM_SPEC_READY")

        if self._cancelled(run_id):
            return
        io_basename = manifest.io.basename
        if checkpoint.get("solution") is not None:
            solution = SolverOutput.model_validate(checkpoint["solution"])
        else:
            self._status(run_id, RunStatus.SOLVING)
            solution = await self.model.solve(problem_spec, io_basename)
            checkpoint["solution"] = solution.model_dump(mode="json")
            self._write(run_id, "solver_output.json", checkpoint["solution"])
            self._save_checkpoint(run_id, checkpoint, "SOLUTION_READY")

        submission = checkpoint.get("submission")
        if submission is None:
            submission = self.workspace.get_or_create_cpp_submission(run_id, problem_id, solution.cpp_source)
            checkpoint["submission"] = submission
            self._save_checkpoint(run_id, checkpoint, "INITIAL_SUBMISSION_READY")
        submission_id = submission["submission_id"]
        revision_id = submission["revision_id"]
        revision_sha = submission["sha256"]
        if self._cancelled(run_id):
            return

        if checkpoint.get("initial") is None:
            review_data = checkpoint.get("initial_reviews")
            if review_data is None:
                self._status(run_id, RunStatus.REVIEWING, revision_id=revision_id)
                algorithm_review, code_review = await self._reviews(problem_spec, solution, io_basename)
                review_data = {
                    "algorithm": algorithm_review.model_dump(mode="json"),
                    "code": code_review.model_dump(mode="json"),
                }
                checkpoint["initial_reviews"] = review_data
                self._save_checkpoint(run_id, checkpoint, "INITIAL_REVIEWS_READY")
            else:
                algorithm_review = CriticReview.model_validate(review_data["algorithm"])
                code_review = CriticReview.model_validate(review_data["code"])

            judge_data = checkpoint.get("initial_judge")
            if judge_data is None:
                self._status(run_id, RunStatus.COMPILING, phase="initial", revision_id=revision_id)
                compile_result, check, frozen = await self._judge_revision(
                    run_id, problem_id, submission_id, revision_id, revision_sha, phase="initial",
                )
                judge_data = {
                    "compile": compile_result.model_dump(mode="json"),
                    "check": check.model_dump(mode="json") if check else None,
                    "frozen": frozen,
                }
                checkpoint["initial_judge"] = judge_data
                self._save_checkpoint(run_id, checkpoint, "INITIAL_JUDGE_READY")
            else:
                compile_result = CompileResult.model_validate(judge_data["compile"])
                check = CheckResult.model_validate(judge_data["check"]) if judge_data.get("check") else None
                frozen = judge_data["frozen"]

            final_reviews = checkpoint.get("initial_final_reviews")
            if final_reviews is None:
                self._status(run_id, RunStatus.LOCALIZING, phase="initial", revision_id=revision_id)
                code_review, initial_code_review = await self._recheck_code_review_if_needed(
                    problem_spec, solution, io_basename, code_review, compile_result, check,
                )
                final_reviews = {
                    "algorithm": algorithm_review.model_dump(mode="json"),
                    "code": code_review.model_dump(mode="json"),
                    "code_initial": initial_code_review.model_dump(mode="json") if initial_code_review else None,
                }
                checkpoint["initial_final_reviews"] = final_reviews
                self._save_checkpoint(run_id, checkpoint, "INITIAL_FINAL_REVIEWS_READY")
            else:
                algorithm_review = CriticReview.model_validate(final_reviews["algorithm"])
                code_review = CriticReview.model_validate(final_reviews["code"])
                initial_code_review = (
                    CriticReview.model_validate(final_reviews["code_initial"])
                    if final_reviews.get("code_initial") else None
                )

            self._write(run_id, "algorithm_critic.json", algorithm_review.model_dump(mode="json"))
            if initial_code_review is not None:
                self._write(run_id, "code_critic_initial.json", initial_code_review.model_dump(mode="json"))
                self._write(run_id, "code_critic_recheck.json", code_review.model_dump(mode="json"))
            self._write(run_id, "code_critic.json", code_review.model_dump(mode="json"))
            diagnosis = adjudicate(
                algorithm_review, code_review, compile_result.verdict, check,
                (step.step_id for step in solution.steps),
            )
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
            stop_reason = "COMPLETED" if is_complete(compile_result.verdict, check, diagnosis) else None
            infrastructure = compile_result.verdict == Verdict.SANDBOX_UNAVAILABLE or (
                check and check.verdict == Verdict.SANDBOX_UNAVAILABLE
            )
            if infrastructure:
                stop_reason = "INFRASTRUCTURE_ERROR"
            if diagnosis.error_type == ErrorType.UNRESOLVED and not diagnosis.final_result_correct:
                stop_reason = stop_reason or "UNRESOLVED"
            checkpoint.update(
                initial=initial, best=best, best_solution=best_solution.model_dump(mode="json"),
                best_key=list(best_key), rounds=rounds, regression_count=regression_count,
                no_improvement=no_improvement, next_round=1, stop_reason=stop_reason,
            )
            self._save_checkpoint(run_id, checkpoint, "INITIAL_EVALUATION_READY")
        else:
            initial = checkpoint["initial"]
            best = checkpoint.get("best", initial)
            best_solution = SolverOutput.model_validate(checkpoint.get("best_solution", checkpoint["solution"]))
            best_key = tuple(checkpoint.get("best_key", (0, 0, 0, 0)))
            rounds = list(checkpoint.get("rounds", []))
            regression_count = int(checkpoint.get("regression_count", 0))
            no_improvement = int(checkpoint.get("no_improvement", 0))
            stop_reason = checkpoint.get("stop_reason")

        requested_rounds = int(request.get("repair", {}).get("max_rounds", self.workspace.settings.repair_max_rounds))
        max_rounds = min(max(1, requested_rounds), self.workspace.settings.repair_hard_max_rounds)
        repair_enabled = bool(request.get("repair", {}).get("enabled", True))
        for round_number in range(int(checkpoint.get("next_round", 1)), max_rounds + 1):
            if stop_reason or not repair_enabled or self._cancelled(run_id):
                break
            inflight = checkpoint.get("inflight_round")
            if not inflight or inflight.get("round_number") != round_number:
                self._status(run_id, RunStatus.REPAIRING, round=round_number, base_revision_id=best["revision_id"])
                repair_plan_id = safe_id("repairplan")
                repair_plan = {
                    "repair_plan_id": repair_plan_id,
                    "target_revision": best["revision_id"],
                    "target_sha256": best["sha256"],
                    "root_cause": best["diagnosis"]["evidence"],
                    "first_error_step_id": best["diagnosis"]["first_error_step_id"],
                    "error_type": best["diagnosis"]["error_type"],
                    "repair_scope": best["diagnosis"]["repair_suggestion"],
                    "required_changes": [best["diagnosis"]["repair_suggestion"]] if best["diagnosis"]["repair_suggestion"] else [],
                    "regression_risks": ["Previously passing tests", "Complexity claim", "Code-step mapping"],
                    "success_criteria": [
                        "Compilation succeeds",
                        "All formal tests pass",
                        "No previously passing test regresses",
                        "Critic findings are structurally consistent and localize the earliest defect",
                        "Every loop and work queue has an explicit termination invariant",
                    ],
                }
                repaired = await self.model.repair(
                    problem_spec, best_solution, best["diagnosis"],
                    best.get("check") or {"verdict": best["compile"]["verdict"]},
                    round_number, io_basename, compile_summary=best["compile"],
                )
                old_source = self.workspace.read_cpp_submission(
                    run_id, submission_id, best["revision_id"],
                )["source_code"]
                diff = "".join(difflib.unified_diff(
                    old_source.splitlines(keepends=True), repaired.cpp_source.splitlines(keepends=True),
                    fromfile="a/main.cpp", tofile="b/main.cpp",
                ))
                if not diff:
                    stop_reason = "STALLED"
                    checkpoint["stop_reason"] = stop_reason
                    self._save_checkpoint(run_id, checkpoint, "REPAIR_STALLED")
                    break
                inflight = {
                    "round_number": round_number,
                    "repair_plan": repair_plan,
                    "repaired": repaired.model_dump(mode="json"),
                    "diff": diff,
                }
                checkpoint["inflight_round"] = inflight
                self._save_checkpoint(run_id, checkpoint, "REPAIR_SOLUTION_READY")
            else:
                repair_plan = inflight["repair_plan"]
                repair_plan_id = repair_plan["repair_plan_id"]
                repaired = SolverOutput.model_validate(inflight["repaired"])
                diff = inflight["diff"]

            revision = inflight.get("revision")
            if revision is None:
                revision = self.workspace.find_repair_revision(run_id, submission_id, repair_plan_id)
                if revision is None:
                    revision = self.workspace.apply_cpp_patch(
                        run_id, submission_id, repair_plan["target_revision"], repair_plan["target_sha256"],
                        diff, round_number, repair_plan_id,
                        repaired.steps[0].statement if repaired.steps else "Hy3 repair",
                    )
                inflight["revision"] = revision
                self._save_checkpoint(run_id, checkpoint, "REPAIR_REVISION_READY")

            judge_data = inflight.get("judge")
            if judge_data is None:
                self._status(
                    run_id, RunStatus.COMPILING, phase="repair",
                    round=round_number, revision_id=revision["revision_id"],
                )
                compile_result, check, frozen = await self._judge_revision(
                    run_id, problem_id, submission_id, revision["revision_id"], revision["sha256"],
                    phase="repair", round_number=round_number,
                )
                judge_data = {
                    "compile": compile_result.model_dump(mode="json"),
                    "check": check.model_dump(mode="json") if check else None,
                    "frozen": frozen,
                }
                inflight["judge"] = judge_data
                self._save_checkpoint(run_id, checkpoint, "REPAIR_JUDGE_READY")
            else:
                compile_result = CompileResult.model_validate(judge_data["compile"])
                check = CheckResult.model_validate(judge_data["check"]) if judge_data.get("check") else None
                frozen = judge_data["frozen"]

            review_data = inflight.get("reviews")
            if review_data is None:
                raw_reviews = inflight.get("reviews_raw")
                if raw_reviews is None:
                    self._status(
                        run_id, RunStatus.REVIEWING, phase="repair",
                        round=round_number, revision_id=revision["revision_id"],
                    )
                    algorithm_review, code_review = await self._reviews(problem_spec, repaired, io_basename)
                    raw_reviews = {
                        "algorithm": algorithm_review.model_dump(mode="json"),
                        "code": code_review.model_dump(mode="json"),
                    }
                    inflight["reviews_raw"] = raw_reviews
                    self._save_checkpoint(run_id, checkpoint, "REPAIR_RAW_REVIEWS_READY")
                else:
                    algorithm_review = CriticReview.model_validate(raw_reviews["algorithm"])
                    code_review = CriticReview.model_validate(raw_reviews["code"])
                code_review, initial_code_review = await self._recheck_code_review_if_needed(
                    problem_spec, repaired, io_basename, code_review, compile_result, check,
                )
                review_data = {
                    "algorithm": algorithm_review.model_dump(mode="json"),
                    "code": code_review.model_dump(mode="json"),
                    "code_initial": initial_code_review.model_dump(mode="json") if initial_code_review else None,
                }
                inflight["reviews"] = review_data
                self._save_checkpoint(run_id, checkpoint, "REPAIR_REVIEWS_READY")
            else:
                algorithm_review = CriticReview.model_validate(review_data["algorithm"])
                code_review = CriticReview.model_validate(review_data["code"])
                initial_code_review = (
                    CriticReview.model_validate(review_data["code_initial"])
                    if review_data.get("code_initial") else None
                )

            self._status(
                run_id, RunStatus.LOCALIZING, phase="repair",
                round=round_number, revision_id=revision["revision_id"],
            )
            diagnosis = adjudicate(
                algorithm_review, code_review, compile_result.verdict, check,
                (step.step_id for step in repaired.steps),
            )
            current = self._evaluation_record(
                revision["revision_id"], revision["sha256"], compile_result, check, diagnosis, frozen,
            )
            current_key = improvement_key(compile_result.verdict, check, diagnosis)
            previous_check = CheckResult.model_validate(best["check"]) if best.get("check") else None
            quality_gate = repair_quality_gate(previous_check, check)
            candidate_rank_improved = current_key > best_key
            improved = candidate_rank_improved and quality_gate["passed"]
            if improved:
                best, best_solution, best_key = current, repaired, current_key
                no_improvement = 0
            else:
                no_improvement += 1
                if current_key < best_key or quality_gate["regressed_tests"]:
                    regression_count += 1
            round_record = {
                "repair_round_id": f"round_{round_number:03d}",
                "parent_revision_id": repair_plan["target_revision"],
                "repair_plan": repair_plan,
                "new_revision_id": revision["revision_id"],
                "new_sha256": revision["sha256"],
                "compile_result": compile_result.model_dump(mode="json"),
                "answer_check_result": check.model_dump(mode="json") if check else None,
                "algorithm_review": algorithm_review.model_dump(mode="json"),
                "code_review": code_review.model_dump(mode="json"),
                "code_review_initial": initial_code_review.model_dump(mode="json") if initial_code_review else None,
                "code_review_recheck_triggered": initial_code_review is not None,
                "process_evaluation": diagnosis.model_dump(mode="json"),
                "quality_gate": {
                    **quality_gate,
                    "candidate_rank_improved": candidate_rank_improved,
                    "algorithm_critic_issues": critic_review_quality_issues(
                        algorithm_review, (step.step_id for step in repaired.steps),
                    ),
                    "code_critic_issues": critic_review_quality_issues(
                        code_review, (step.step_id for step in repaired.steps),
                    ),
                },
                "improved": improved,
                "loop_decision": "COMPLETE" if is_complete(compile_result.verdict, check, diagnosis) else "CONTINUE",
            }
            rounds.append(round_record)
            self._write(run_id, f"repair_rounds/round_{round_number:03d}/round.json", round_record)
            if is_complete(compile_result.verdict, check, diagnosis):
                best, best_solution, best_key = current, repaired, current_key
                stop_reason = "COMPLETED"
            elif compile_result.verdict == Verdict.SANDBOX_UNAVAILABLE or (
                check and check.verdict == Verdict.SANDBOX_UNAVAILABLE
            ):
                stop_reason = "INFRASTRUCTURE_ERROR"
            elif diagnosis.error_type == ErrorType.UNRESOLVED:
                stop_reason = "UNRESOLVED"
            elif no_improvement >= self.workspace.settings.stop_after_no_improvement_rounds:
                stop_reason = "STALLED"
            checkpoint.update(
                best=best,
                best_solution=best_solution.model_dump(mode="json"),
                best_key=list(best_key),
                rounds=rounds,
                regression_count=regression_count,
                no_improvement=no_improvement,
                next_round=round_number + 1,
                stop_reason=stop_reason,
            )
            checkpoint.pop("inflight_round", None)
            self._save_checkpoint(run_id, checkpoint, "REPAIR_ROUND_READY")

        if self._cancelled(run_id):
            return
        stop_reason = stop_reason or ("MAX_ROUNDS" if repair_enabled else "REPAIR_DISABLED")
        final_result = {
            "run_id": run_id,
            "problem_id": problem_id,
            "resource_binding_id": checkpoint.get("binding_id", binding_id),
            "submission_id": submission_id,
            "initial_submission_result": initial,
            "repair_round_results": rounds,
            "best_submission_result": best,
            "final_submission_result": best,
            "repair_success": not initial["complete"] and best["complete"],
            "repair_round_count": len(rounds),
            "regression_count": regression_count,
            "stop_reason": stop_reason,
            "difficulty": manifest.luogu_difficulty,
            "difficulty_status": "PENDING_USER_LABEL" if manifest.luogu_difficulty is None else "LABELED",
            "completed_at": utc_now(),
        }
        self._write(
            run_id, "best_revision.json",
            {"revision_id": best["revision_id"], "sha256": best["sha256"], "selection_key": list(best_key)},
        )
        self._write(run_id, "final_evaluation.json", final_result)
        checkpoint["final_result"] = final_result
        checkpoint["stop_reason"] = stop_reason
        self._save_checkpoint(run_id, checkpoint, "READY_TO_FINALIZE")
        status = RunStatus.COMPLETED if stop_reason in {
            "COMPLETED", "MAX_ROUNDS", "STALLED", "UNRESOLVED", "REPAIR_DISABLED",
        } else RunStatus.FAILED
        self.store.update_run(
            run_id, status.value, result=final_result,
            event={"stop_reason": stop_reason, "best_revision_id": best["revision_id"]},
        )
        self._save_checkpoint(run_id, checkpoint, "FINALIZED")

    @staticmethod
    def _evaluation_record(revision_id: str, sha256: str, compile_result: CompileResult, check: CheckResult | None, diagnosis: Diagnosis, frozen: dict[str, Any]) -> dict[str, Any]:
        return {
            "revision_id": revision_id, "sha256": sha256, "source_artifact_id": frozen["source_artifact_id"],
            "compile": compile_result.model_dump(mode="json"), "check": check.model_dump(mode="json") if check else None,
            "diagnosis": diagnosis.model_dump(mode="json"), "complete": is_complete(compile_result.verdict, check, diagnosis),
        }
