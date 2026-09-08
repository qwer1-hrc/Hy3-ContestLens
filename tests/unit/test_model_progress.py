import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from hy3_contestlens.domain import RunStatus
from hy3_contestlens.model import Hy3Client
from hy3_contestlens.settings import Hy3Settings
from hy3_contestlens.store import Store
from hy3_contestlens.workflow import ContestWorkflow


@pytest.mark.asyncio
async def test_persisted_progress_is_grouped_by_stage_and_parallel_call(settings):
    async def respond(request):
        role = json.loads(request.content)["messages"][0]["content"].split("Current isolated role: ")[1].rstrip(".")
        await asyncio.sleep(.01)
        if role == "solver":
            result = {"problem_summary": "scan", "steps": [{"step_id": "S1", "goal": "sum", "statement": "scan", "justification": "induction"}],
                      "complexity": {"time": "O(n)", "space": "O(1)"}, "cpp_source": "int main() {}"}
        else:
            result = {"reviewer": role, "assessments": [], "summary": "review"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}, "finish_reason": "stop"}]})

    store = Store(settings.database_path)
    model = Hy3Client(Hy3Settings(api_key="TEST_ONLY", reasoning_effort="high"), transport=httpx.MockTransport(respond))
    workflow = ContestWorkflow(store, None, SimpleNamespace(settings=settings), None, model, None)
    run_id = store.create_run("road", {"problem_id": "road"})["run_id"]

    async def execute(rid):
        workflow._status(rid, RunStatus.SOLVING)
        solution = await model.solve({}, "road")
        workflow._status(rid, RunStatus.REVIEWING)
        await workflow._reviews({}, solution, "road")

    workflow._execute = execute
    await workflow.execute(run_id)
    events = store.list_events(run_id)
    parents = {event["seq"]: event["type"] for event in events}
    progress = [event["data"] for event in events if event["type"] == "MODEL_CALL_PROGRESS"]
    assert {p["role"] for p in progress} == {"solver", "algorithm_critic", "code_critic"}
    for p in progress:
        assert parents[p["parent_seq"]] == ("SOLVING" if p["role"] == "solver" else "REVIEWING")
    critics = [p for p in progress if p["role"] != "solver"]
    assert [p["phase"] for p in critics[:2]] == ["waiting", "waiting"]
    assert len({p["call_id"] for p in critics}) == 2
    assert sum(p["phase"] == "success" for p in progress) == 3
    assert "TEST_ONLY" not in json.dumps(events)
