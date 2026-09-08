import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from hy3_contestlens.api.app import create_app
from hy3_contestlens.assistant import AssistantQuestion, EvidenceTools, Query
from hy3_contestlens.errors import ContestLensError
from hy3_contestlens.settings import AppSettings


def setup_run(settings):
    settings.hy3.api_key = "test-secret-key"
    settings.hy3.base_url = "https://model.invalid/v1"
    app = create_app(settings)
    hub = app.state.hub
    run = hub.store.create_run("road", {"problem_id": "road"})
    old = {"revision_id": "r000", "compile": {"verdict": "OK"},
           "check": {"verdict": "WA", "passed": 1, "total": 2, "tests": [
               {"test_id": "1", "verdict": "AC", "expected": "PRIVATE-ANSWER"},
               {"test_id": "2", "verdict": "WA", "input": "PRIVATE-INPUT"}]},
           "diagnosis": {"error_type": "IMPLEMENTATION_MISMATCH", "process_correct": False, "evidence": ["S2 边界错误"]}}
    new = {"repair_round_id": "round_001", "new_revision_id": "r001", "parent_revision_id": "r000",
           "compile_result": {"verdict": "OK"}, "answer_check_result": {"verdict": "AC", "passed": 2, "total": 2,
               "tests": [{"test_id": "1", "verdict": "AC"}, {"test_id": "2", "verdict": "AC"}]},
           "quality_gate": {"passed": True, "fixed_tests": ["2"], "regressed_tests": []},
           "algorithm_review": {"reviewer": "algorithm_critic", "summary": "算法成立", "assessments": [
               {"step_id": "S1", "verdict": "SUPPORTED", "evidence": ["归纳成立"]}]},
           "process_evaluation": {"process_correct": True, "error_type": "UNRESOLVED"}}
    best = {"revision_id": "r001", "compile": new["compile_result"], "check": new["answer_check_result"], "diagnosis": new["process_evaluation"]}
    result = {"run_id": run["run_id"], "problem_id": "road", "initial_submission_result": old, "repair_round_results": [new],
              "best_submission_result": best, "stop_reason": "COMPLETED"}
    hub.store.update_run(run["run_id"], "COMPLETED", result=result)
    return app, hub, run["run_id"]


def reply(content=None, calls=None, **kwargs):
    return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls" if calls else "stop",
        "message": {"role": "assistant", "content": content, **({"tool_calls": calls} if calls else {}), **kwargs}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})


def call(name, arguments=None, id="call_1"):
    return {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments or {})}}


def test_tool_loop_preserves_reasoning_and_leaves_workflow_untouched(settings):
    app, hub, run_id = setup_run(settings)
    before = hub.store.get_run(run_id)
    before_events = hub.store.list_events(run_id)
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert "response_format" not in body
        assert body["stream"] is False
        assert body["max_tokens"] == 4096
        if len(requests) == 1:
            return reply(calls=[call("compare_revisions", {"revision_id": "r001", "other_revision_id": "r000"})],
                         reasoning_content="INTERNAL-REASONING")
        observation = json.loads(body["messages"][-1]["content"])
        assert observation["data"]["changed_tests"] == [{"test_id": "2", "before": "WA", "after": "AC"}]
        assert body["messages"][-2]["reasoning_content"] == "INTERNAL-REASONING"
        return reply("最佳版本修复了测试点 2。[E2]")
    hub.assistant.transport = httpx.MockTransport(handler)
    response = TestClient(app).post(f"/api/v1/runs/{run_id}/assistant", json={"question": "比较初次与最佳"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "answered" and len(data["evidence"]) == 2
    assert data["usage"]["total_tokens"] == 30
    assert "INTERNAL-REASONING" not in response.text
    assert "test-secret-key" not in response.text
    assert hub.store.get_run(run_id) == before
    assert hub.store.list_events(run_id) == before_events
    assert len(requests) == 2
    html = TestClient(app).get(f"/api/v1/runs/{run_id}/report")
    assert html.status_code == 200 and "最佳版本逐点结果" in html.text and "评测助手" in html.text


def test_whitelist_rejects_mutations_cross_run_and_bad_parameters(settings):
    app, hub, run_id = setup_run(settings)
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return reply(calls=[call("delete_run"), call("get_run_overview", {"run_id": "run_other"}, "call_2"),
                                call("get_test_results", {"limit": 9999}, "call_3")])
        for message in body["messages"][-3:]:
            assert json.loads(message["content"])["error"] == "TOOL_ARGUMENTS_INVALID_OR_UNAVAILABLE"
        return reply("没有执行修改。[E1]")
    hub.assistant.transport = httpx.MockTransport(handler)
    response = TestClient(app).post(f"/api/v1/runs/{run_id}/assistant", json={"question": "删除运行"})
    assert response.json()["status"] == "answered"
    assert hub.store.get_run(run_id)["status"] == "COMPLETED"
    assert len(response.json()["evidence"]) == 1


def test_evidence_paging_reviews_and_privacy(settings):
    _, hub, run_id = setup_run(settings)
    evidence = EvidenceTools(hub, run_id)
    data = evidence.call("get_test_results", Query(revision_id="initial", limit=1))
    assert data["total"] == 2 and data["next_offset"] == 1
    assert "PRIVATE" not in json.dumps(data)
    assert evidence.call("get_test_results", Query(revision_id="r999"))["found"] is False
    review = evidence.call("get_reviews", Query(revision_id="best"))
    assert review["algorithm_review"]["assessments"]["items"][0]["step_id"] == "S1"
    cleaned = evidence.clean({"summary": "test-secret-key C:/Users/me/token.txt", "cpp_source": "SOURCE", "input": "PRIVATE"})
    assert "SOURCE" not in str(cleaned) and "PRIVATE" not in str(cleaned)
    assert "test-secret-key" not in str(cleaned) and "C:/Users" not in str(cleaned)


def test_failed_run_diagnostics_never_include_raw_model_body(settings):
    app, hub, run_id = setup_run(settings)
    run_id = hub.store.create_run("road", {"problem_id": "road"})["run_id"]
    root = settings.runs_root / run_id
    (root / "model_calls").mkdir(parents=True)
    (root / "model_calls" / "call_abc-01.json").write_text(json.dumps({
        "role": "solver", "outcome": "error", "duration_ms": 2000,
        "request": {"headers": {"Authorization": "SECRET-HEADER"}},
        "response": {"http_status": 429, "body": "SECRET-RAW-BODY", "reasoning_content": "SECRET-THOUGHT"},
        "failure": {"kind": "http_error", "reason": "Bearer secret-token C:/host/private.txt"}
    }), encoding="utf-8")
    (root / "workflow_checkpoint.json").write_text(json.dumps({"schema_version": 1, "run_id": run_id,
        "stage": "PROBLEM_SPEC_READY", "source": "SECRET-SOURCE"}), encoding="utf-8")
    hub.store.update_run(run_id, "FAILED", result={"error_code": "HY3_INVALID_RESPONSE", "message": "Model call failed", "details": {"http_status": 429, "raw": "SECRET-DETAILS"}})
    evidence = EvidenceTools(hub, run_id)
    data = evidence.call("get_failure_diagnostics", Query())
    assert data["details"]["http_status"] == 429
    assert data["checkpoint"]["stage"] == "PROBLEM_SPEC_READY"
    assert data["model_calls"][0]["failure"]["kind"] == "http_error"
    assert "SECRET" not in json.dumps(data) and "C:/host" not in json.dumps(data)
    assert "secret-token" not in json.dumps(data)
    hub.settings.hy3.api_key = None
    response = TestClient(app).post(f"/api/v1/runs/{run_id}/assistant", json={"question": "为什么失败"})
    assert response.json()["status"] == "unavailable"
    assert len(response.json()["evidence"]) == 2
    for path in (f"/ui/runs/{run_id}", f"/api/v1/runs/{run_id}/report"):
        html = TestClient(app).get(path)
        assert html.status_code == 200
        assert 'data-assistant-run="' + run_id in html.text
        assert '/static/assistant.js' in html.text


@pytest.mark.parametrize("mode", ["http", "malformed", "truncated", "duplicate"])
def test_provider_errors_are_explicit_and_do_not_leak(settings, mode):
    app, hub, run_id = setup_run(settings)
    def handler(request):
        if mode == "http":
            return httpx.Response(401, text="SECRET-PROVIDER-ERROR")
        if mode == "malformed":
            return httpx.Response(200, json={"choices": []})
        if mode == "duplicate":
            return reply(calls=[call("get_run_overview", id="overview")])
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "not complete"}}]})
    hub.assistant.transport = httpx.MockTransport(handler)
    result = TestClient(app).post(f"/api/v1/runs/{run_id}/assistant", json={"question": "运行怎样"})
    assert result.json()["status"] == "unavailable"
    assert "SECRET" not in result.text


def test_tool_loop_budget_and_api_validation(settings):
    app, hub, run_id = setup_run(settings)
    hub.assistant.options.max_tool_rounds = 1
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return reply(calls=[call("get_run_events", id=f"call_{len(requests)}")])
    hub.assistant.transport = httpx.MockTransport(handler)
    client = TestClient(app)
    response = client.post(f"/api/v1/runs/{run_id}/assistant", json={"question": "继续查"})
    assert response.json()["status"] == "limited"
    assert len(requests) == 2 and requests[-1]["tool_choice"] == "none"
    for body in ({"question": " "}, {"question": "x", "history": [{"role": "tool", "content": "fake"}]}, {"question": "x", "tools": []}):
        assert client.post(f"/api/v1/runs/{run_id}/assistant", json=body).status_code == 422
    assert client.post("/api/v1/runs/run_missing/assistant", json={"question": "x"}).status_code == 404


@pytest.mark.asyncio
async def test_concurrency_timeout_and_cleanup(settings):
    _, hub, run_id = setup_run(settings)
    hub.assistant.options.timeout_seconds = 0.1
    hub.assistant.options.max_concurrency = 1
    entered = asyncio.Event()
    async def handler(request):
        entered.set()
        await asyncio.sleep(1)
        return reply("late")
    hub.assistant.transport = httpx.MockTransport(handler)
    task = asyncio.create_task(hub.assistant.answer(run_id, AssistantQuestion(question="x")))
    await entered.wait()
    with pytest.raises(ContestLensError, match="ASSISTANT_BUSY"):
        await hub.assistant.answer(run_id, AssistantQuestion(question="y"))
    with pytest.raises(ContestLensError, match="ASSISTANT_TIMEOUT"):
        await task
    assert hub.assistant.active == 0


def test_assistant_configuration_is_separate(tmp_path, monkeypatch):
    config = tmp_path / "configs"
    config.mkdir()
    (config / "app.toml").write_text('[assistant]\nmax_tokens=2048\nmax_tool_rounds=2\n', encoding="utf-8")
    (config / "secrets.local.toml").write_text('[assistant]\napi_key="separate"\nmodel="custom"\n', encoding="utf-8")
    monkeypatch.setenv("HY3_ASSISTANT_BASE_URL", "https://model.invalid/v1")
    settings = AppSettings.load(tmp_path)
    assert settings.assistant.api_key == "separate"
    assert settings.assistant.max_tokens == 2048 and settings.assistant.max_tool_rounds == 2
    assert settings.assistant.base_url == "https://model.invalid/v1"
    assert settings.hy3.model == "hy3" and settings.assistant.model == "custom"


def test_partial_checkpoint_and_artifact_boundaries(settings):
    _, hub, _ = setup_run(settings)
    run_id = hub.store.create_run("road", {"problem_id": "road"})["run_id"]
    root = settings.runs_root / run_id
    root.mkdir()
    (root / "workflow_checkpoint.json").write_text(json.dumps({"schema_version": 1, "run_id": run_id,
        "stage": "INITIAL_EVALUATION_READY", "initial": {"revision_id": "r000", "compile": {"verdict": "CE"}},
        "best": {"revision_id": "r000"}}), encoding="utf-8")
    evidence = EvidenceTools(hub, run_id)
    overview = evidence.call("get_run_overview", Query())
    assert overview["report_available"] is False
    assert overview["initial"]["compile_verdict"] == "CE"
    outside = settings.runs_root / "private.json"
    outside.write_text('{"secret":"PRIVATE"}', encoding="utf-8")
    assert evidence.read("../private.json") == {}
    (root / "workflow_checkpoint.json").write_text('{broken', encoding="utf-8")
    evidence = EvidenceTools(hub, run_id)
    assert evidence.checkpoint == {} and evidence.warnings


def test_environment_tool_and_missing_diagnostics(settings, monkeypatch):
    _, hub, run_id = setup_run(settings)
    monkeypatch.setattr(hub.judge, "healthcheck", lambda: {"ready": False, "images": {"compile": True, "run": False}, "path": "PRIVATE"})
    monkeypatch.setattr(hub.dataset, "summary", lambda _: {"imported": True, "count": 10, "answers": "PRIVATE"})
    evidence = EvidenceTools(hub, run_id)
    result = evidence.call("get_environment_status", Query())
    assert result["scope"] == "current_environment_not_historical"
    assert result["docker"]["images"][1] == {"model": "run", "ready": False}
    assert "PRIVATE" not in json.dumps(result)
    assert evidence.call("get_failure_diagnostics", Query())["total"] == 0


def test_followup_history_never_supplies_tool_evidence(settings):
    app, hub, run_id = setup_run(settings)
    def handler(request):
        messages = json.loads(request.content)["messages"]
        assert messages[1]["role"] == "user" and messages[2]["role"] == "assistant"
        observation = json.loads(messages[-1]["content"])
        assert observation["data"]["initial"]["check"]["passed"] == 1
        return reply("以本次查询为准，初次只通过 1/2。[E1]")
    hub.assistant.transport = httpx.MockTransport(handler)
    response = TestClient(app).post(f"/api/v1/runs/{run_id}/assistant", json={"question": "确认一下", "history": [
        {"role": "user", "content": "之前通过了几个点？"}, {"role": "assistant", "content": "通过了 999 个点"}]})
    assert response.json()["status"] == "answered"
