from __future__ import annotations

import asyncio
import csv
import io
import json
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, Header, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field

from ..errors import ContestLensError
from ..reporting import aggregate_runs, render_run_report
from ..service import ServiceHub
from ..settings import AppSettings


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    app = FastAPI(title="Hy3-ContestLens", version="0.1.0", description="Hy3 process evaluation and error localization for NOIP 2018")
    app.state.hub = hub

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
            "private_dataset": {"ready": sum(item["count"] for item in private.values()) == 120, "problems": private},
        }
        ready = all(value.get("ready", False) for value in components.values())
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"ready": ready, "components": components}

    @app.get("/api/v1/system/capabilities")
    def capabilities() -> dict[str, Any]:
        return {
            "schema_version": 1, "dataset": "noip2018", "problem_count": 6,
            "interfaces": ["REST", "CLI", "MCP", "WebUI"], "sandbox": "linux_docker_only",
            "mcp_servers": ["resources", "workspace", "judge"], "max_repair_rounds": hub.settings.repair_hard_max_rounds,
        }

    @app.get("/api/v1/datasets")
    def datasets() -> list[dict[str, Any]]:
        return [{"dataset_id": "noip2018", "problem_count": 6, "test_count": sum(hub.dataset.summary(item.problem_id)["count"] for item in hub.catalog.list())}]

    def problem_view(problem_id: str) -> dict[str, Any]:
        manifest = hub.catalog.get(problem_id)
        memory, source = hub.catalog.effective_memory(problem_id)
        return {
            **manifest.model_dump(mode="json"), "effective_memory_limit_mb": memory,
            "memory_limit_source": source, "difficulty_status": "PENDING_USER_LABEL" if manifest.luogu_difficulty is None else "LABELED",
        }

    @app.get("/api/v1/datasets/noip2018/problems")
    def problems() -> list[dict[str, Any]]:
        return [problem_view(item.problem_id) for item in hub.catalog.list()]

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
        hub.catalog.get(body.problem_id)
        binding = hub.store.get_binding(body.problem_id)
        if body.resource_binding_id and binding["binding_id"] != body.resource_binding_id:
            raise ContestLensError("RESOURCE_BINDING_MISMATCH", "Requested binding is not current", status_code=409)
        return hub.store.create_run(body.problem_id, body.model_dump(mode="json"), idempotency_key=idempotency_key)

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        return hub.store.get_run(run_id)

    @app.post("/api/v1/runs/{run_id}/start", status_code=202)
    async def start_run(run_id: str) -> dict[str, Any]:
        run = hub.store.get_run(run_id)
        if run["status"] not in {"CREATED", "FAILED"}:
            return {"run_id": run_id, "status": run["status"], "idempotent": True}
        hub.start_run(run_id)
        return {"run_id": run_id, "status": "QUEUED"}

    @app.post("/api/v1/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> dict[str, Any]:
        return hub.store.cancel_run(run_id)

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
    def run_report(run_id: str) -> str:
        return render_run_report(run_result(run_id))

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
        if run["status"] == "CREATED":
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
    app.mount("/static", StaticFiles(directory=str(static_root)), name="static")

    def page(request: Request, name: str, **context: Any):
        return templates.TemplateResponse(request=request, name=name, context={"request": request, "problems": [problem_view(item.problem_id) for item in hub.catalog.list()], **context})

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return page(request, "index.html")

    @app.get("/ui/resources", response_class=HTMLResponse)
    def resources_page(request: Request):
        return page(request, "resources.html", scopes=hub.store.list_scopes())

    @app.get("/ui/runs/new", response_class=HTMLResponse)
    def new_run_page(request: Request):
        return page(request, "new_run.html")

    @app.get("/ui/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str):
        return page(request, "run_detail.html", run_id=run_id)

    @app.get("/ui/submissions/{submission_id}", response_class=HTMLResponse)
    def submission_page(request: Request, submission_id: str, run_id: str):
        return page(request, "submission_detail.html", submission_id=submission_id, run_id=run_id)

    @app.get("/ui/reports", response_class=HTMLResponse)
    def reports_page(request: Request):
        return page(request, "reports.html")

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
