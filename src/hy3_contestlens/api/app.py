from __future__ import annotations

import asyncio
import csv
import io
import json
import hmac
import secrets
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, Header, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..errors import ContestLensError
from ..assistant import AssistantQuestion
from ..reporting import aggregate_runs, has_evaluation_report, render_run_markdown, render_run_report, run_failure_summary
from ..report_translation import REPORT_LABELS
from ..service import ServiceHub
from ..settings import AppSettings


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportTranslationRequest(APIModel):
    priority_run_id: str | None = None
    retry_run_id: str | None = None


class BulkRunActionRequest(APIModel):
    run_ids: list[str] = Field(min_length=1, max_length=200)
    action: Literal["hide", "show", "delete"]
    action_token: str
    confirmation: str | None = None

    @field_validator("run_ids")
    @classmethod
    def valid_unique_run_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or any(not value.startswith("run_") for value in values):
            raise ValueError("run_ids must be unique run identifiers")
        return values


class BulkReportExportRequest(APIModel):
    run_ids: list[str] = Field(min_length=1, max_length=200)
    format: Literal["html", "md"]
    action_token: str

    @field_validator("run_ids")
    @classmethod
    def unique_run_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("run_ids must be unique")
        return values


class ValidatePathRequest(APIModel):
    path: str


class GrantPathRequest(APIModel):
    path: str
    confirmed: bool


class DiscoverRequest(APIModel):
    problem_id: str
    mode: Literal["auto"] = "auto"
    preferred_document: str | None = None
    search_tests: bool = True


class BindRequest(APIModel):
    scope_id: str
    candidate_id: str


class RepairOptions(APIModel):
    enabled: bool = True
    max_rounds: int = Field(default=3, ge=1, le=5)


class CreateRunRequest(APIModel):
    problem_id: str
    resource_binding_id: str | None = None
    repair: RepairOptions = Field(default_factory=RepairOptions)
    image_understanding: Literal["ask", "use", "skip"] = "skip"


class ImageUnderstandingChoice(APIModel):
    request_id: str
    choice: Literal["use", "skip"]


class SubmissionRequest(APIModel):
    problem_id: str
    source_code: str
    created_by: str = "external_client"


class ReplaceRequest(APIModel):
    base_revision_id: str
    base_sha256: str
    source_code: str
    repair_round: int = Field(ge=1, le=5)
    repair_plan_id: str
    reason: str


class PatchRequest(APIModel):
    base_revision_id: str
    base_sha256: str
    patch: str
    repair_round: int = Field(ge=1, le=5)
    repair_plan_id: str
    reason: str


class FreezeRequest(APIModel):
    expected_sha256: str


class CompileRequest(APIModel):
    problem_id: str
    source_artifact_id: str
    source_sha256: str
    compile_profile: str = "noip2018_cpp"


class CheckRequest(APIModel):
    compile_artifact_id: str
    dataset_id: str = "noip2018"
    problem_id: str
    test_ids: list[str] | None = None


class BenchmarkRequest(APIModel):
    problem_ids: list[str] = Field(default_factory=lambda: ["road", "money", "track", "travel", "game", "defense"])
    repair: RepairOptions = Field(default_factory=RepairOptions)


class AnnotationTaskRequest(APIModel):
    case_id: str
    problem_id: str
    material: dict[str, Any]
    system_prediction: dict[str, Any] | None = None


class AnnotationLabelRequest(APIModel):
    annotator: str
    final_result_correct: bool
    process_correct: bool
    first_error_step_id: str | None = None
    error_type: str
    notes: str = ""


class AdjudicationRequest(APIModel):
    adjudicator: str
    final_result_correct: bool
    process_correct: bool
    first_error_step_id: str | None = None
    error_type: str
    notes: str = ""


def create_app(settings: AppSettings | None = None) -> FastAPI:
    hub = ServiceHub(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await hub.start()
        try:
            yield
        finally:
            await hub.close()

    app = FastAPI(title="Hy3-ContestLens", version="0.1.0", description="Hy3 process evaluation and error localization for NOIP 2018", lifespan=lifespan)
    app.state.hub = hub
    app.state.ui_action_token = secrets.token_urlsafe(24)

    @app.exception_handler(ContestLensError)
    async def contestlens_error(_: Request, exc: ContestLensError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error_code": exc.code, "message": exc.message, "details": exc.details})

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"status": "ok", "service": "hy3-contestlens", "version": "0.1.0"}

    @app.get("/readyz")
    def readyz(response: Response) -> dict[str, Any]:
        docker = hub.judge.healthcheck()
        database = {"ready": hub.store.ping()}
        hy3 = {"ready": hub.settings.hy3.configured, **hub.settings.hy3.safe_summary()}
        private = {item.problem_id: hub.dataset.summary(item.problem_id) for item in hub.catalog.list()}
        components = {
            "database": database, "hy3": hy3, "resources_mcp": {"ready": True},
            "workspace_mcp": {"ready": True}, "judge_mcp": {"ready": True}, "docker": docker,
            "private_dataset": {"ready": bool(private) and all(item["imported"] or hub.catalog.get(pid).data_status == "missing" for pid,item in private.items()), "problems": private, "missing_data": [pid for pid,item in private.items() if not item["imported"]]},
        }
        ready = all(value.get("ready", False) for value in components.values())
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"ready": ready, "components": components}

    @app.get("/api/v1/system/capabilities")
    def capabilities() -> dict[str, Any]:
        return {
            "schema_version": 1, "dataset": "contest_collection", "problem_count": len(hub.catalog.list()),
            "interfaces": ["REST", "CLI", "MCP", "WebUI"], "sandbox": "linux_docker_only",
            "mcp_servers": ["resources", "workspace", "judge"], "max_repair_rounds": hub.settings.repair_hard_max_rounds,
            "image_understanding": hub.settings.image_understanding.safe_summary(),
        }

    @app.get("/api/v1/datasets")
    def datasets() -> list[dict[str, Any]]:
        manifests = hub.catalog.list()
        return [{"dataset_id": key, "problem_count": sum(m.dataset_id == key for m in manifests), "test_count": sum(hub.dataset.summary(m.problem_id)["count"] for m in manifests if m.dataset_id == key)} for key in sorted({m.dataset_id for m in manifests})]

    def problem_view(problem_id: str) -> dict[str, Any]:
        manifest = hub.catalog.get(problem_id)
        memory, source = hub.catalog.effective_memory(problem_id)
        return {
            **manifest.model_dump(mode="json"), "effective_memory_limit_mb": memory,
            "memory_limit_source": source, "difficulty_status": "PENDING_USER_LABEL" if manifest.luogu_difficulty is None else "LABELED",
        }

    @app.get("/api/v1/problems")
    def problems() -> list[dict[str, Any]]:
        return [problem_view(item.problem_id) for item in hub.catalog.list()]

    @app.get("/api/v1/datasets/{dataset_id}/problems")
    def dataset_problems(dataset_id: str) -> list[dict[str, Any]]:
        selected = [problem_view(m.problem_id) for m in hub.catalog.list() if m.dataset_id == dataset_id]
        if not selected:
            raise ContestLensError("DATASET_NOT_FOUND", "Unknown dataset", status_code=404)
        return selected

    @app.get("/api/v1/problems/{problem_id}/statement")
    def statement(problem_id: str) -> dict[str, Any]:
        manifest = hub.catalog.get(problem_id)
        try:
            binding = hub.store.get_binding(problem_id)
        except ContestLensError as exc:
            if exc.code != "RESOURCE_BINDING_NOT_FOUND" or not manifest.statement_relative_path:
                raise
            for scope in hub.store.list_scopes():
                try:
                    path = hub.resources.resolve_scoped(scope["scope_id"], manifest.statement_relative_path, extensions={".md"})
                    return {"problem_id":problem_id, "content":path.read_text(encoding="utf-8"), "content_type":"untrusted_problem_content"}
                except ContestLensError:
                    continue
            raise exc
        document = binding["document"]
        chunks, cursor = [], 0
        while True:
            chunk = hub.resources.read_problem_document(binding["scope_id"], document, cursor=cursor)
            chunks.append(chunk["content"])
            if chunk["next_cursor"] is None:
                break
            cursor = chunk["next_cursor"]
        return {"problem_id": problem_id, "content": "\n".join(chunks), "content_type": "untrusted_problem_content"}

    @app.get("/api/v1/problems/{problem_id}")
    def get_problem(problem_id: str) -> dict[str, Any]:
        return problem_view(problem_id)

    @app.get("/api/v1/problems/{problem_id}/tests/summary")
    def test_summary(problem_id: str) -> dict[str, Any]:
        return hub.dataset.summary(problem_id)

    @app.post("/api/v1/resource-scopes:validate")
    def validate_scope(body: ValidatePathRequest) -> dict[str, Any]:
        return hub.resources.validate_host_path(body.path)

    @app.post("/api/v1/resource-scopes", status_code=201)
    def grant_scope(body: GrantPathRequest) -> dict[str, Any]:
        return hub.resources.grant_host_path(body.path, body.confirmed)

    @app.get("/api/v1/resource-scopes")
    def list_scopes() -> list[dict[str, Any]]:
        return hub.store.list_scopes()

    @app.get("/api/v1/resource-scopes/{scope_id}")
    def inspect_scope(scope_id: str) -> dict[str, Any]:
        return hub.resources.inspect_scope(scope_id)

    @app.post("/api/v1/resource-scopes/{scope_id}:discover")
    def discover(scope_id: str, body: DiscoverRequest) -> dict[str, Any]:
        manifest = hub.catalog.get(body.problem_id)
        return hub.resources.find_problem_assets(scope_id, body.problem_id, manifest.title_zh, manifest.io.basename)

    @app.post("/api/v1/problems/{problem_id}/resource-binding", status_code=201)
    def bind(problem_id: str, body: BindRequest) -> dict[str, Any]:
        candidate = hub.resources.get_candidate(body.candidate_id)
        return hub.resources.bind_candidate(body.scope_id, problem_id, candidate)

    @app.get("/api/v1/problems/{problem_id}/resource-binding")
    def get_binding(problem_id: str) -> dict[str, Any]:
        return hub.store.get_binding(problem_id)

    @app.post("/api/v1/problems/{problem_id}/resource-binding:rediscover")
    def rediscover(problem_id: str) -> dict[str, Any]:
        binding = hub.store.get_binding(problem_id)
        manifest = hub.catalog.get(problem_id)
        return hub.resources.find_problem_assets(binding["scope_id"], problem_id, manifest.title_zh, manifest.io.basename)

    @app.post("/api/v1/runs", status_code=201)
    def create_run(body: CreateRunRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
        manifest = hub.catalog.get(body.problem_id)
        if manifest.judge_note:
            raise ContestLensError("UNSUPPORTED_COMPARATOR", manifest.judge_note, status_code=422)
        binding = hub.store.get_binding(body.problem_id)
        if body.resource_binding_id and binding["binding_id"] != body.resource_binding_id:
            raise ContestLensError("RESOURCE_BINDING_MISMATCH", "Requested binding is not current", status_code=409)
        return hub.store.create_run(body.problem_id, body.model_dump(mode="json"), idempotency_key=idempotency_key)

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        return hub.store.get_run(run_id)

    @app.post("/api/v1/runs/{run_id}/assistant")
    async def ask_run_assistant(run_id: str, body: AssistantQuestion) -> dict[str, Any]:
        return await hub.assistant.answer(run_id, body)

    @app.post("/api/v1/runs/{run_id}/start", status_code=202)
    async def start_run(run_id: str) -> dict[str, Any]:
        run = hub.store.get_run(run_id)
        if run["status"] not in {"CREATED", "FAILED", "INTERRUPTED"}:
            # This also repairs a queued/active run whose previous lease has expired.
            hub.start_run(run_id, queue_if_needed=False)
            return {"run_id": run_id, "status": run["status"], "idempotent": True}
        hub.start_run(run_id)
        return {"run_id": run_id, "status": "QUEUED"}

    @app.post("/api/v1/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> dict[str, Any]:
        return hub.store.cancel_run(run_id)

    @app.post("/api/v1/runs/{run_id}/image-understanding")
    def image_understanding_choice(run_id: str, body: ImageUnderstandingChoice) -> dict[str, Any]:
        return hub.store.decide_image_understanding(run_id, body.request_id, body.choice)

    @app.get("/api/v1/runs/{run_id}/events")
    def events(run_id: str, after_seq: int = 0) -> dict[str, Any]:
        items = hub.store.list_events(run_id, after_seq)
        return {"run_id": run_id, "events": items, "next_seq": items[-1]["seq"] if items else after_seq}

    @app.get("/api/v1/runs/{run_id}/events/stream")
    async def event_stream(run_id: str, after_seq: int = 0) -> StreamingResponse:
        async def generate():
            cursor = after_seq
            while True:
                items = hub.store.list_events(run_id, cursor)
                for item in items:
                    cursor = item["seq"]
                    yield json.dumps(item, ensure_ascii=False) + "\n"
                current = hub.store.get_run(run_id)
                if current["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                await asyncio.sleep(1)
        return StreamingResponse(generate(), media_type="application/x-ndjson")

    @app.get("/api/v1/runs/{run_id}/result")
    def run_result(run_id: str) -> dict[str, Any]:
        run = hub.store.get_run(run_id)
        if run["result"] is None:
            raise ContestLensError("RUN_NOT_FINISHED", "Run has no final result yet", {"status": run["status"]}, 409)
        return run["result"]

    @app.get("/api/v1/runs/{run_id}/report", response_class=HTMLResponse)
    def run_report(request: Request, run_id: str, priority: bool = False):
        result = run_result(run_id)
        available = has_evaluation_report(result)
        document = hub.report_translations.document(run_id)
        response = page(
            request, "report_detail.html", run_id=run_id, result=result,
            report_available=available, translation=document["state"], sections=document["sections"],
            priority=priority, back_url=f"/ui/runs/{run_id}" if priority else "/ui/reports",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v1/runs/{run_id}/report/export")
    def export_run_report(run_id: str, format: Literal["html", "md"] = "html"):
        result = run_result(run_id)
        if not has_evaluation_report(result):
            raise ContestLensError("REPORT_NOT_AVAILABLE", "Run has no complete report", status_code=409)
        content = render_run_report(result) if format == "html" else render_run_markdown(result)
        media = "text/html; charset=utf-8" if format == "html" else "text/markdown; charset=utf-8"
        filename = f"{result['problem_id']}-{run_id}.{format}"
        return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    def require_ui_token(token: str) -> None:
        if not hmac.compare_digest(token, app.state.ui_action_token):
            raise ContestLensError("UI_ACTION_TOKEN_INVALID", "Refresh the report list and try again", status_code=403)

    @app.post("/ui/runs/{run_id}/report-visibility")
    def set_report_visibility(run_id: str, hidden: bool, action_token: str, include_hidden: bool = False):
        require_ui_token(action_token)
        hub.store.set_run_report_hidden(run_id, hidden)
        return RedirectResponse(f"/ui/reports?include_hidden={'true' if include_hidden else 'false'}", status_code=303)

    @app.post("/ui/runs/{run_id}:delete")
    async def delete_run_page(run_id: str, confirmation: str, action_token: str, include_hidden: bool = False):
        require_ui_token(action_token)
        if confirmation != "permanent":
            raise ContestLensError("PERMANENT_CONFIRMATION_REQUIRED", "Permanent deletion must be confirmed", status_code=400)
        await hub.delete_run(run_id)
        return RedirectResponse(f"/ui/reports?include_hidden={'true' if include_hidden else 'false'}", status_code=303)

    @app.post("/api/v1/report-actions")
    async def bulk_report_action(body: BulkRunActionRequest) -> dict[str, Any]:
        require_ui_token(body.action_token)
        if body.action == "delete":
            if body.confirmation != "permanent":
                raise ContestLensError("PERMANENT_CONFIRMATION_REQUIRED", "Permanent deletion must be confirmed", status_code=400)
            return await hub.delete_runs(body.run_ids)
        return hub.store.set_runs_report_hidden(body.run_ids, body.action == "hide")

    @app.post("/api/v1/report-exports")
    def bulk_report_export(body: BulkReportExportRequest) -> Response:
        require_ui_token(body.action_token)
        buffer = io.BytesIO()
        skipped = []
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for run_id in body.run_ids:
                run = hub.store.get_run(run_id)
                if not has_evaluation_report(run["result"]):
                    skipped.append(run_id)
                    continue
                content = render_run_report(run["result"]) if body.format == "html" else render_run_markdown(run["result"])
                archive.writestr(f"{run['problem_id']}-{run_id}.{body.format}", content.encode("utf-8"))
            if skipped:
                archive.writestr("未导出的运行.txt", ("以下运行没有完整报告：\n" + "\n".join(skipped) + "\n").encode("utf-8"))
        return Response(buffer.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="contestlens-reports-{body.format}.zip"'})

    @app.post("/api/v1/report-translations", status_code=202)
    async def start_report_translations(body: ReportTranslationRequest) -> dict[str, Any]:
        return hub.report_translations.enqueue(priority_run_id=body.priority_run_id, retry_run_id=body.retry_run_id)

    @app.get("/api/v1/report-translations")
    def report_translation_status() -> dict[str, Any]:
        # Polling is read-only: it cannot enqueue work or invoke the model.
        return hub.report_translations.snapshot()

    @app.get("/api/v1/runs/{run_id}/diagnosis")
    def run_diagnosis(run_id: str) -> dict[str, Any]:
        result = run_result(run_id)
        return result["best_submission_result"]["diagnosis"]

    @app.get("/api/v1/runs/{run_id}/repair-rounds")
    def repair_rounds(run_id: str) -> list[dict[str, Any]]:
        return run_result(run_id)["repair_round_results"]

    @app.get("/api/v1/runs/{run_id}/repair-rounds/{round_id}")
    def repair_round(run_id: str, round_id: str) -> dict[str, Any]:
        rounds = repair_rounds(run_id)
        item = next((value for value in rounds if value["repair_round_id"] == round_id), None)
        if item is None:
            raise ContestLensError("REPAIR_ROUND_NOT_FOUND", "Repair round does not exist", {"round_id": round_id}, 404)
        return item

    @app.post("/api/v1/runs/{run_id}/repair", status_code=202)
    async def trigger_repair(run_id: str) -> dict[str, Any]:
        run = hub.store.get_run(run_id)
        if run["status"] in {"CREATED", "INTERRUPTED"}:
            hub.start_run(run_id)
            return {"run_id": run_id, "status": "QUEUED", "mode": "automatic_bounded_loop"}
        raise ContestLensError("REPAIR_ALREADY_MANAGED", "Repair rounds are managed by the active deterministic loop controller", {"status": run["status"]}, 409)

    @app.post("/api/v1/runs/{run_id}/repair:continue")
    def continue_repair(run_id: str) -> dict[str, Any]:
        run = hub.store.get_run(run_id)
        raise ContestLensError("RUN_RESUME_REQUIRES_NEW_RUN", "A stopped immutable run cannot be rewritten; create a new run to continue from its exported best revision", {"run_id": run_id, "status": run["status"]}, 409)

    @app.post("/api/v1/runs/{run_id}/repair:stop")
    def stop_repair(run_id: str) -> dict[str, Any]:
        return hub.store.cancel_run(run_id)

    @app.get("/api/v1/runs/{run_id}/revisions/{revision_id}/evaluation")
    def revision_evaluation(run_id: str, revision_id: str) -> dict[str, Any]:
        result = run_result(run_id)
        initial = result["initial_submission_result"]
        if initial["revision_id"] == revision_id:
            return initial
        for item in result["repair_round_results"]:
            if item["new_revision_id"] == revision_id:
                return item
        raise ContestLensError("REVISION_EVALUATION_NOT_FOUND", "No evaluation exists for this revision", {"revision_id": revision_id}, 404)

    @app.post("/api/v1/runs/{run_id}/submissions", status_code=201)
    def create_submission(run_id: str, body: SubmissionRequest) -> dict[str, Any]:
        hub.store.get_run(run_id)
        return hub.workspace.create_cpp_submission(run_id, body.problem_id, body.source_code, body.created_by)

    @app.get("/api/v1/runs/{run_id}/submissions/{submission_id}")
    def submission(run_id: str, submission_id: str) -> dict[str, Any]:
        return hub.workspace.list_cpp_revisions(run_id, submission_id)

    @app.get("/api/v1/runs/{run_id}/submissions/{submission_id}/revisions")
    def revisions(run_id: str, submission_id: str) -> dict[str, Any]:
        return hub.workspace.list_cpp_revisions(run_id, submission_id)

    @app.get("/api/v1/runs/{run_id}/submissions/{submission_id}/revisions/{revision_id}")
    def revision(run_id: str, submission_id: str, revision_id: str) -> dict[str, Any]:
        return hub.workspace.read_cpp_submission(run_id, submission_id, revision_id)

    @app.post("/api/v1/runs/{run_id}/submissions/{submission_id}/revisions:replace", status_code=201)
    def replace_revision(run_id: str, submission_id: str, body: ReplaceRequest) -> dict[str, Any]:
        return hub.workspace.replace_cpp_submission(run_id, submission_id, body.base_revision_id, body.base_sha256, body.source_code, body.repair_round, body.repair_plan_id, body.reason)

    @app.post("/api/v1/runs/{run_id}/submissions/{submission_id}/revisions:patch", status_code=201)
    def patch_revision(run_id: str, submission_id: str, body: PatchRequest) -> dict[str, Any]:
        return hub.workspace.apply_cpp_patch(run_id, submission_id, body.base_revision_id, body.base_sha256, body.patch, body.repair_round, body.repair_plan_id, body.reason)

    @app.post("/api/v1/runs/{run_id}/submissions/{submission_id}/revisions/{revision_id}:freeze", status_code=201)
    def freeze_revision(run_id: str, submission_id: str, revision_id: str, body: FreezeRequest) -> dict[str, Any]:
        return hub.workspace.freeze_cpp_revision(run_id, submission_id, revision_id, body.expected_sha256)

    @app.post("/api/v1/judge/compile")
    def compile_source(body: CompileRequest) -> dict[str, Any]:
        return hub.judge.compile_cpp(body.problem_id, body.source_artifact_id, body.source_sha256, body.compile_profile).model_dump(mode="json")

    @app.get("/api/v1/judge/compilations/{compile_artifact_id}")
    def compilation(compile_artifact_id: str) -> dict[str, Any]:
        return hub.judge.get_compile(compile_artifact_id)

    @app.post("/api/v1/judge/check-answer")
    def check_answer(body: CheckRequest) -> dict[str, Any]:
        return hub.judge.check_answer(body.compile_artifact_id, body.dataset_id, body.problem_id, body.test_ids).model_dump(mode="json")

    @app.get("/api/v1/judge/checks/{check_id}")
    def check(check_id: str) -> dict[str, Any]:
        return hub.judge.get_check(check_id)

    @app.get("/api/v1/judge/checks/{check_id}/tests")
    def check_tests(check_id: str) -> list[dict[str, Any]]:
        return hub.judge.get_check(check_id)["tests"]

    @app.post("/api/v1/benchmarks", status_code=202)
    async def create_benchmark(body: BenchmarkRequest) -> dict[str, Any]:
        for problem_id in body.problem_ids:
            hub.catalog.get(problem_id)
            hub.store.get_binding(problem_id)
        runs = []
        for problem_id in body.problem_ids:
            created = hub.store.create_run(problem_id, {"problem_id": problem_id, "repair": body.repair.model_dump(mode="json")})
            runs.append(created["run_id"])
        benchmark = hub.store.create_benchmark({"dataset_id": "noip2018", "problem_ids": body.problem_ids, "run_ids": runs})
        for run_id in runs:
            hub.start_run(run_id)
        return benchmark

    @app.get("/api/v1/benchmarks/{benchmark_id}")
    def get_benchmark(benchmark_id: str) -> dict[str, Any]:
        benchmark = hub.store.get_benchmark(benchmark_id)
        states = [hub.store.get_run(run_id) for run_id in benchmark["run_ids"]]
        terminal = all(item["status"] in {"COMPLETED", "FAILED", "CANCELLED"} for item in states)
        return {**benchmark, "status": "COMPLETED" if terminal else "RUNNING", "runs": [{"run_id": item["run_id"], "problem_id": item["problem_id"], "status": item["status"]} for item in states]}

    @app.get("/api/v1/benchmarks/{benchmark_id}/results")
    def benchmark_results(benchmark_id: str) -> dict[str, Any]:
        benchmark = hub.store.get_benchmark(benchmark_id)
        states = [hub.store.get_run(run_id) for run_id in benchmark["run_ids"]]
        if not all(item["result"] is not None for item in states):
            raise ContestLensError("BENCHMARK_NOT_FINISHED", "Benchmark runs are not all finished", status_code=409)
        valid = [item["result"] for item in states if item["status"] == "COMPLETED"]
        return {"benchmark_id": benchmark_id, "summary": aggregate_runs(valid), "runs": valid, "failed_runs": [item["run_id"] for item in states if item["status"] != "COMPLETED"]}

    @app.get("/api/v1/benchmarks/{benchmark_id}/artifacts/{artifact_name}")
    def benchmark_artifact(benchmark_id: str, artifact_name: str) -> Response:
        results = benchmark_results(benchmark_id)
        if artifact_name == "summary.json":
            return Response(json.dumps(results, ensure_ascii=False, indent=2), media_type="application/json")
        if artifact_name == "evaluation_results.csv":
            stream = io.StringIO()
            writer = csv.DictWriter(stream, fieldnames=["run_id", "problem_id", "initial_passed", "initial_total", "final_passed", "final_total", "process_correct", "error_type", "stop_reason"])
            writer.writeheader()
            for item in results["runs"]:
                initial = item["initial_submission_result"].get("check") or {}
                final = item["best_submission_result"].get("check") or {}
                writer.writerow({"run_id": item["run_id"], "problem_id": item["problem_id"], "initial_passed": initial.get("passed", 0), "initial_total": initial.get("total", 0), "final_passed": final.get("passed", 0), "final_total": final.get("total", 0), "process_correct": item["best_submission_result"]["diagnosis"]["process_correct"], "error_type": item["best_submission_result"]["diagnosis"]["error_type"], "stop_reason": item["stop_reason"]})
            return Response(stream.getvalue(), media_type="text/csv; charset=utf-8")
        raise ContestLensError("BENCHMARK_ARTIFACT_NOT_FOUND", "Supported artifacts are summary.json and evaluation_results.csv", {"artifact_name": artifact_name}, 404)

    @app.post("/api/v1/annotations/tasks", status_code=201)
    def create_annotation(body: AnnotationTaskRequest) -> dict[str, Any]:
        return hub.store.create_annotation_task(body.model_dump(mode="json"))

    @app.get("/api/v1/annotations/tasks/{task_id}")
    def annotation_task(task_id: str) -> dict[str, Any]:
        return hub.store.get_annotation_task(task_id)

    @app.post("/api/v1/annotations/tasks/{task_id}/labels", status_code=201)
    def annotation_label(task_id: str, body: AnnotationLabelRequest) -> dict[str, Any]:
        data = body.model_dump(mode="json")
        annotator = data.pop("annotator")
        return hub.store.add_annotation_label(task_id, annotator, data)

    @app.post("/api/v1/annotations/tasks/{task_id}/adjudication")
    def annotation_adjudication(task_id: str, body: AdjudicationRequest) -> dict[str, Any]:
        return hub.store.adjudicate_annotation(task_id, body.model_dump(mode="json"))

    @app.get("/api/v1/annotations/export")
    def annotations_export() -> list[dict[str, Any]]:
        return hub.store.export_annotations()

    templates_root = hub.settings.project_root / "src" / "hy3_contestlens" / "web" / "templates"
    static_root = hub.settings.project_root / "src" / "hy3_contestlens" / "web" / "static"
    templates = Jinja2Templates(directory=str(templates_root))
    templates.env.filters["report_label"] = lambda value: REPORT_LABELS.get(value, value or "—")
    app.mount("/static", StaticFiles(directory=str(static_root)), name="static")

    def page(request: Request, name: str, **context: Any):
        return templates.TemplateResponse(request=request, name=name, context={"request": request, "problems": [problem_view(item.problem_id) for item in hub.catalog.list()], "ui_action_token": app.state.ui_action_token, **context})

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return page(request, "index.html")

    @app.get("/ui/resources", response_class=HTMLResponse)
    def resources_page(request: Request):
        return page(request, "resources.html", scopes=hub.store.list_scopes())

    @app.get("/ui/runs/new", response_class=HTMLResponse)
    def new_run_page(request: Request):
        return page(request, "new_run.html")

    @app.get("/ui/problems/{problem_id}", response_class=HTMLResponse)
    def problem_page(request: Request, problem_id: str):
        return page(request, "problem_detail.html", problem=problem_view(problem_id), statement=statement(problem_id)["content"])

    @app.get("/ui/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str):
        run = hub.store.get_run(run_id)
        return page(request, "run_detail.html", run_id=run_id, run=run, problem=problem_view(run["problem_id"]))

    @app.get("/ui/submissions/{submission_id}", response_class=HTMLResponse)
    def submission_page(request: Request, submission_id: str, run_id: str):
        return page(request, "submission_detail.html", submission_id=submission_id, run_id=run_id)

    @app.get("/ui/reports", response_class=HTMLResponse)
    def reports_page(request: Request, include_hidden: bool = False):
        titles = {item.problem_id: item.title_zh for item in hub.catalog.list()}
        status_labels = {
            "CREATED": "已创建", "QUEUED": "排队中", "INTERRUPTED": "等待恢复", "DISCOVERING_RESOURCES": "发现资源",
            "WAITING_FOR_RESOURCE_CONFIRMATION": "等待资源确认", "ANALYZING": "分析题目",
            "SOLVING": "生成解法", "REVIEWING": "双路盲审", "COMPILING": "编译中",
            "JUDGING": "评测中", "LOCALIZING": "定位错误", "REPAIRING": "修复中",
            "REJUDGING": "重新评测", "COMPLETED": "已完成", "FAILED": "运行失败",
            "CANCELLED": "已取消",
        }
        empty_reasons = {
            "CREATED": "尚未启动", "QUEUED": "等待后台接管", "INTERRUPTED": "服务重启后将从检查点恢复", "FAILED": "运行失败，未生成报告",
            "CANCELLED": "已取消，未生成报告", "COMPLETED": "未生成完整评测报告",
        }
        report_rows = []
        all_runs = hub.store.list_runs(include_hidden=True)
        for run in (all_runs if include_hidden else [item for item in all_runs if not item["report_hidden"]]):
            created_at = datetime.fromisoformat(run["created_at"])
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            report_rows.append({
                "run_id": run["run_id"], "problem_id": run["problem_id"],
                "title_zh": titles.get(run["problem_id"], run["problem_id"]),
                "created_at": run["created_at"],
                "created_at_display": created_at.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
                "status_label": status_labels.get(run["status"], run["status"]),
                "status_tone": {"COMPLETED": "success", "FAILED": "danger", "CANCELLED": "muted"}.get(run["status"], "active"),
                "report_available": has_evaluation_report(run["result"]),
                "translation": hub.report_translations.status(run["run_id"]),
                "empty_reason": empty_reasons.get(run["status"], "等待运行完成"),
                "failure_summary": run_failure_summary(run["result"]) if run["status"] == "FAILED" else None,
                "report_hidden": run["report_hidden"], "terminal": run["status"] in {"COMPLETED", "FAILED", "CANCELLED"},
            })
        return page(
            request, "reports.html", runs=report_rows,
            report_count=sum(row["report_available"] for row in report_rows),
            include_hidden=include_hidden, hidden_count=sum(run["report_hidden"] for run in all_runs),
        )

    @app.get("/ui/annotations/{task_id}", response_class=HTMLResponse)
    def annotation_page(request: Request, task_id: str):
        return page(request, "annotation.html", task_id=task_id)

    return app


app = create_app()


def main() -> None:
    settings = AppSettings.load()
    uvicorn.run("hy3_contestlens.api.app:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
