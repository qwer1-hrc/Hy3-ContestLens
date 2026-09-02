import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from hy3_contestlens.datasets import ManifestCatalog
from hy3_contestlens.domain import CheckResult, CompileResult, CriticReview, SolverOutput, Verdict
from hy3_contestlens.errors import ContestLensError
from hy3_contestlens.model import Hy3Client
from hy3_contestlens.problem_spec import Analysis, build_problem_spec
from hy3_contestlens.settings import Hy3Settings
from hy3_contestlens.store import Store
from hy3_contestlens.workflow import ContestWorkflow
from hy3_contestlens.workspace import WorkspaceStore


VALID = {
    "summary": "Count binary boards subject to path order constraints.",
    "inputs": ["Two positive integers n and m."],
    "outputs": ["The count modulo 1000000007; public sample 2 2 -> 12."],
    "constraints": ["For every pair: w(P1)>w(P2) implies s(P1)<=s(P2).", "n<=8, m<=1000000"],
    "boundary_cases": [], "likely_structures": [], "source_references": ["PAGE 4", "PAGE 5", "PAGE 6"],
}
EMPTY = {key: "" if key == "summary" else [] for key in VALID}
DOCUMENT = {"document_id": "game-doc", "sha256": "original-hash", "page_start": 4, "page_end": 6,
            "content_type": "untrusted_problem_content", "content": "Original statement: w(P1)>w(P2) implies s(P1)<=s(P2). Public sample 5 5 -> 7136."}
METADATA = {"problem_id": "game", "source": {"document_id": "game-doc", "sha256": "original-hash"}}


@pytest.mark.parametrize("field,value", [
    ("summary", ""), ("summary", " \n\t"), ("inputs", []), ("outputs", []),
    ("constraints", []), ("source_references", []), ("inputs", ["   "]),
    ("constraints", ["\t"]), ("outputs", [""]), ("boundary_cases", [""]),
])
def test_analysis_rejects_present_but_empty_fields(field, value):
    with pytest.raises(ValidationError):
        Analysis.model_validate({**VALID, field: value})


def test_original_statement_survives_a_lossy_but_nonempty_summary():
    spec = build_problem_spec(VALID, DOCUMENT, METADATA)
    assert "7136" not in spec["summary"]
    assert spec["source_document"]["content"] == DOCUMENT["content"]
    assert spec["source_document"]["content_type"] == "untrusted_problem_content"
    assert spec["source_document"]["sha256"] == "original-hash"
    assert spec["source"] == METADATA["source"]
    assert spec["public_metadata"]["problem_id"] == "game"
    assert "source_document" not in VALID


def test_empty_source_is_not_replaced_by_an_invented_analysis():
    with pytest.raises(ContestLensError, match="PROBLEM_DOCUMENT_EMPTY"):
        build_problem_spec(VALID, {**DOCUMENT, "content": "  "}, METADATA)


def model_client(tmp_path, replies):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        content = replies[len(requests) - 1]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}]})

    client = Hy3Client(Hy3Settings(api_key="test-key", retry_backoff_seconds=0), diagnostics_dir=tmp_path,
                       transport=httpx.MockTransport(respond))
    return client, requests


@pytest.mark.asyncio
async def test_recorded_game_empty_analysis_is_retried_and_corrected(tmp_path):
    client, requests = model_client(tmp_path, [EMPTY, VALID])
    assert await client.analyze_problem(DOCUMENT, METADATA) == VALID
    assert len(requests) == 2
    schema = requests[0]["response_format"]["json_schema"]
    assert schema["name"] == "Analysis"
    assert schema["schema"]["properties"]["summary"]["minLength"] == 1
    assert schema["schema"]["properties"]["inputs"]["minItems"] == 1
    feedback = requests[1]["messages"][-1]["content"]
    assert "summary" in feedback and "constraints" in feedback
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(tmp_path.glob("call_*.json"))]
    assert records[0]["failure"]["kind"] == "schema_validation"
    assert records[1]["outcome"] == "success"


def workflow_for(settings, model):
    store = Store(settings.database_path)
    run_id = store.create_run("game", {"repair": {"enabled": True, "max_rounds": 1}})["run_id"]
    store.get_binding = lambda _: {"binding_id": "binding-test", "scope_id": "scope", "document": {}}
    resources = SimpleNamespace(read_problem_document=lambda *args, **kwargs: {**DOCUMENT, "next_cursor": None})
    workflow = ContestWorkflow(store, resources, WorkspaceStore(settings), None, model, ManifestCatalog(settings.manifests_root))

    async def no_images(run_id, binding, document, mode):
        return document

    workflow._prepare_images = no_images
    return workflow, run_id


@pytest.mark.asyncio
async def test_three_empty_analyses_fail_before_spending_calls_on_solver_or_repairs(settings, tmp_path):
    client, requests = model_client(tmp_path, [EMPTY, EMPTY, EMPTY])
    workflow, run_id = workflow_for(settings, client)
    await workflow.execute(run_id)
    run = workflow.store.get_run(run_id)
    assert run["status"] == "FAILED"
    assert run["result"]["details"]["role"] == "problem_analyst"
    assert run["result"]["details"]["failure_kind"] == "schema_validation"
    assert len(requests) == 3
    assert all("problem_analyst" in request["messages"][0]["content"] for request in requests)
    assert not (settings.runs_root / run_id / "solver_output.json").exists()
    assert (settings.runs_root / run_id / "problem_document.json").exists()


@pytest.mark.asyncio
async def test_workflow_guard_also_rejects_empty_analysis_from_custom_clients(settings):
    async def analyze(*args):
        return EMPTY

    workflow, run_id = workflow_for(settings, SimpleNamespace(analyze_problem=analyze))
    await workflow.execute(run_id)
    run = workflow.store.get_run(run_id)
    assert run["result"]["error_code"] == "PROBLEM_ANALYSIS_INCOMPLETE"
    assert run["status"] == "FAILED"
    assert not any(event["type"] == "SOLVING" for event in workflow.store.list_events(run_id))


@pytest.mark.asyncio
async def test_full_statement_reaches_solver_both_reviews_and_repair(settings):
    calls = []

    def solution(value):
        return SolverOutput(
            problem_summary="Synthetic pipeline verification", steps=[{"step_id": "S1", "goal": "test", "statement": "test", "justification": "test"}],
            complexity={"time": "O(1)", "space": "O(1)"},
            cpp_source='#include <cstdio>\n// [STEP S1]\nint main() { freopen("game.in", "r", stdin); freopen("game.out", "w", stdout); '
                       + f'printf("%d\\n", {value}); return 0; }}\n',
        )

    class Model:
        async def analyze_problem(self, *args):
            return VALID

        async def solve(self, spec, *args):
            calls.append(("solve", spec))
            return solution(0)

        async def algorithm_review(self, spec, *args):
            calls.append(("algorithm_review", spec))
            return CriticReview(reviewer="algorithm_critic", summary="Synthetic review")

        async def code_review(self, spec, *args, **kwargs):
            calls.append(("code_review", spec))
            return CriticReview(reviewer="code_critic", summary="Synthetic review")

        async def repair(self, spec, *args, **kwargs):
            calls.append(("repair", spec))
            return solution(1)

    class Judge:
        calls = 0

        def compile_cpp(self, problem, artifact, source_sha):
            return CompileResult(verdict=Verdict.OK, source_artifact_id=artifact, compile_artifact_id="mock-compile")

        def check_answer(self, *args):
            self.calls += 1
            correct = self.calls > 1
            return CheckResult(check_id=f"check_{self.calls}", verdict=Verdict.AC if correct else Verdict.WA,
                               problem_id="game", dataset_id="noip2018", total=1, passed=int(correct), score=100 if correct else 0)

    workflow, run_id = workflow_for(settings, Model())
    workflow.judge = Judge()
    await workflow.execute(run_id)
    run = workflow.store.get_run(run_id)
    assert run["status"] == "COMPLETED", run["result"]
    assert run["result"]["best_submission_result"]["check"]["verdict"] == "AC"
    assert {name for name, spec in calls} == {"solve", "algorithm_review", "code_review", "repair"}
    assert all(spec["source_document"]["content"] == DOCUMENT["content"] for _, spec in calls)
    assert all(spec["source_document"]["content_type"] == "untrusted_problem_content" for _, spec in calls)

    # Also verify the real prompt builders retain the original in every role, not only the workflow arguments.
    class CapturingClient(Hy3Client):
        async def json_completion(self, *, role, prompt, schema):
            assert DOCUMENT["content"] in prompt
            return CriticReview(reviewer="algorithm_critic" if role == "algorithm_critic" else "code_critic", summary="test") if schema is CriticReview else solution(0)

    client = CapturingClient(Hy3Settings())
    spec = calls[0][1]
    await client.solve(spec, "game")
    await client.algorithm_review(spec, solution(0))
    await client.code_review(spec, solution(0), "game")
    await client.repair(spec, solution(0), {}, {}, 1, "game")
