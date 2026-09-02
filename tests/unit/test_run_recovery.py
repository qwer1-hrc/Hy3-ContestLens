from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from hy3_contestlens.datasets import ManifestCatalog
from hy3_contestlens.domain import CheckResult, CompileResult, CriticReview, SolverOutput, Verdict
from hy3_contestlens.service import ServiceHub
from hy3_contestlens.store import Store
from hy3_contestlens.workflow import ContestWorkflow
from hy3_contestlens.workspace import WorkspaceStore


DOCUMENT = {
    "document_id": "doc_game",
    "sha256": "statement-sha",
    "content": "Synthetic game statement",
    "content_type": "untrusted_problem_content",
    "next_cursor": None,
}
ANALYSIS = {
    "summary": "Synthetic complete analysis",
    "inputs": ["n"],
    "outputs": ["answer"],
    "constraints": ["n is positive"],
    "boundary_cases": ["n=1"],
    "likely_structures": ["constant"],
    "source_references": ["doc_game"],
}


@pytest.mark.asyncio
async def test_service_shutdown_marks_interrupted_and_next_instance_recovers(settings):
    first = ServiceHub(settings)
    run_id = first.store.create_run("road", {"problem_id": "road"})["run_id"]
    started = asyncio.Event()

    async def blocked(rid):
        first.store.update_run(rid, "SOLVING")
        started.set()
        await asyncio.Event().wait()

    first.workflow = SimpleNamespace(execute=blocked)
    await first.start()
    assert first.start_run(run_id)
    await asyncio.wait_for(started.wait(), timeout=2)
    await first.close()
    assert first.store.get_run(run_id)["status"] == "INTERRUPTED"

    second = ServiceHub(settings)
    completed = asyncio.Event()

    async def finish(rid):
        second.store.update_run(rid, "COMPLETED", result={"stop_reason": "COMPLETED"})
        completed.set()

    second.workflow = SimpleNamespace(execute=finish)
    await second.start()
    await asyncio.wait_for(completed.wait(), timeout=2)
    assert second.store.get_run(run_id)["status"] == "COMPLETED"
    assert second.store.get_run(run_id)["attempt"] == 2
    await second.close()


@pytest.mark.asyncio
async def test_startup_recovers_a_hard_crash_after_lease_expiry(settings):
    store = Store(settings.database_path)
    run_id = store.create_run("road", {})["run_id"]
    store.queue_run(run_id)
    assert store.claim_run(run_id, "dead_runner", lease_seconds=-1)
    store.update_run(run_id, "SOLVING")

    hub = ServiceHub(settings)
    completed = asyncio.Event()

    async def finish(rid):
        hub.store.update_run(rid, "COMPLETED", result={"stop_reason": "COMPLETED"})
        completed.set()

    hub.workflow = SimpleNamespace(execute=finish)
    await hub.start()
    await asyncio.wait_for(completed.wait(), timeout=2)
    assert hub.store.get_run(run_id)["attempt"] == 2
    await hub.close()


@pytest.mark.asyncio
async def test_checkpoint_resume_does_not_repeat_analysis_or_solver(settings):
    store = Store(settings.database_path)
    run_id = store.create_run("game", {"repair": {"enabled": False, "max_rounds": 1}})["run_id"]
    binding = {"binding_id": "binding_game", "scope_id": "scope", "document": {}}
    store.get_binding = lambda _: binding
    calls = {"analysis": 0, "solve": 0, "review": 0}
    reviews_started = asyncio.Event()
    block_reviews = True

    solution = SolverOutput(
        problem_summary="Synthetic solution",
        steps=[{"step_id": "S1", "goal": "answer", "statement": "Print zero", "justification": "fixture"}],
        complexity={"time": "O(1)", "space": "O(1)"},
        cpp_source=(
            '#include <cstdio>\n// [STEP S1]\nint main(){freopen("game.in","r",stdin);'
            'freopen("game.out","w",stdout);printf("0\\n");return 0;}\n'
        ),
    )

    class Model:
        async def analyze_problem(self, *_):
            calls["analysis"] += 1
            return ANALYSIS

        async def solve(self, *_):
            calls["solve"] += 1
            return solution

        async def algorithm_review(self, *_):
            return await self._review("algorithm_critic")

        async def code_review(self, *_, **__):
            return await self._review("code_critic")

        async def _review(self, reviewer):
            nonlocal block_reviews
            calls["review"] += 1
            if calls["review"] >= 2:
                reviews_started.set()
            if block_reviews:
                await asyncio.Event().wait()
            return CriticReview(reviewer=reviewer, summary="supported")

    class Judge:
        def compile_cpp(self, _problem, artifact, _sha):
            return CompileResult(verdict=Verdict.OK, source_artifact_id=artifact, compile_artifact_id="compile_ok")

        def check_answer(self, _compile, _dataset, problem):
            return CheckResult(
                check_id="check_ok", verdict=Verdict.AC, problem_id=problem,
                dataset_id="noip2018", passed=1, total=1, score=100,
            )

    resources = SimpleNamespace(read_problem_document=lambda *_, **__: dict(DOCUMENT))
    model = Model()

    def workflow():
        item = ContestWorkflow(
            store, resources, WorkspaceStore(settings), Judge(), model,
            ManifestCatalog(settings.manifests_root),
        )

        async def no_images(_run_id, _binding, document, _mode):
            return document

        item._prepare_images = no_images
        return item

    interrupted = asyncio.create_task(workflow()._execute(run_id))
    await asyncio.wait_for(reviews_started.wait(), timeout=2)
    interrupted.cancel()
    with pytest.raises(asyncio.CancelledError):
        await interrupted

    checkpoint = json.loads((settings.runs_root / run_id / "workflow_checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["stage"] == "INITIAL_SUBMISSION_READY"
    assert checkpoint["solution"]["problem_summary"] == "Synthetic solution"

    block_reviews = False
    await workflow()._execute(run_id)
    state = store.get_run(run_id)
    assert state["status"] == "COMPLETED"
    assert state["result"]["stop_reason"] == "COMPLETED"
    assert calls["analysis"] == 1
    assert calls["solve"] == 1
    assert any(event["type"] == "RUN_RESUMED" for event in store.list_events(run_id))
