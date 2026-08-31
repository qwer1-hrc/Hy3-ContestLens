import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from hy3_contestlens.api.app import create_app
from hy3_contestlens.utils import atomic_write_json


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def evaluation_result(run_id, problem_id):
    evaluation = {
        "check": None,
        "diagnosis": {"process_correct": False, "error_type": "COMPILE_ERROR"},
    }
    return {
        "run_id": run_id, "problem_id": problem_id, "stop_reason": "REPAIR_DISABLED",
        "initial_submission_result": evaluation, "best_submission_result": evaluation,
    }


def test_reports_page_has_useful_empty_state(client):
    response = client.get("/ui/reports")
    assert response.status_code == 200
    assert "暂无运行记录" in response.text
    assert 'href="/ui/runs/new"' in response.text


def test_reports_list_all_runs_newest_first_and_only_link_available_reports(client, monkeypatch):
    store = client.app.state.hub.store
    specifications = [
        ("road", "COMPLETED", True),
        ("money", "FAILED", False),
        ("track", "SOLVING", False),
        ("travel", "CANCELLED", False),
        ("game", "FAILED", True),
    ]
    runs = []
    for index, (problem_id, status, has_report) in enumerate(specifications):
        timestamp = f"2026-08-29T16:0{index}:00+00:00"
        monkeypatch.setattr("hy3_contestlens.store.utc_now", lambda value=timestamp: value)
        run = store.create_run(problem_id, {"problem_id": problem_id})
        result = evaluation_result(run["run_id"], problem_id) if has_report else None
        if status == "FAILED" and not has_report:
            result = {"error_code": "HY3_NOT_CONFIGURED", "message": "private diagnostic"}
        store.update_run(run["run_id"], status, result=result)
        runs.append(run)

    response = client.get("/ui/reports")
    assert response.status_code == 200
    html = response.text
    assert "货币系统" in html
    assert "2026-08-30 00:00:00" in html
    assert "2026-08-30 00:04:00" in html
    assert "北京时间" in html
    assert "2 份报告可查看" in html
    assert html.count('class="report-empty-label">空</span>') == 3
    assert "运行失败，未生成报告" in html
    assert "等待运行完成" in html
    assert "已取消，未生成报告" in html
    assert "private diagnostic" not in html
    positions = [html.index(f'data-run-id="{run["run_id"]}"') for run in reversed(runs)]
    assert positions == sorted(positions)

    for run, (_, _, has_report) in zip(runs, specifications):
        report_url = f'/api/v1/runs/{run["run_id"]}/report'
        assert (f'href="{report_url}"' in html) is has_report
        assert f'href="/ui/runs/{run["run_id"]}"' in html
        if has_report:
            report = client.get(report_url)
            assert report.status_code == 200
            assert "Hy3-ContestLens 评测报告" in report.text
            assert run["run_id"] in report.text


def test_reports_keep_incomplete_and_unknown_problem_records(client):
    store = client.app.state.hub.store
    run = store.create_run("retired_problem", {"problem_id": "retired_problem"})
    store.update_run(run["run_id"], "COMPLETED", result={"initial_submission_result": {}})
    response = client.get("/ui/reports")
    assert response.status_code == 200
    assert "retired_problem" in response.text
    assert "未生成完整评测报告" in response.text
    assert f'/api/v1/runs/{run["run_id"]}/report' not in response.text


def test_report_reads_are_inert_then_explicit_post_translates_renders_and_reuses(client, settings):
    hub = client.app.state.hub
    run = hub.store.create_run("road", {"problem_id": "road"})
    run_id = run["run_id"]
    result = evaluation_result(run_id, "road")
    result["initial_submission_result"]["diagnosis"]["evidence"] = ["deterministic_judge_verdict=WA", "Original English evidence."]
    hub.store.update_run(run_id, "COMPLETED", result=result)
    atomic_write_json(settings.runs_root / run_id / "algorithm_critic.json", {
        "summary": "Original English critic summary.", "error_type": "PROOF_GAP",
        "assessments": [{"step_id": "S1", "verdict": "UNSUPPORTED", "confidence": 0.7, "evidence": ["Original critic evidence."]}],
    })
    calls = []

    async def translate(texts):
        calls.append(texts)
        return {key: "中文依据 <script>alert(1)</script>" for key in texts}

    hub.report_translations.model = SimpleNamespace(settings=SimpleNamespace(model="mock"), translate_report_texts=translate)
    report_url = f"/api/v1/runs/{run_id}/report"
    for url in ("/ui/reports", report_url, f"{report_url}?priority=true", "/api/v1/report-translations"):
        assert client.get(url).status_code == 200
    assert calls == [] and hub.report_translations._worker is None
    assert "返回运行详情" in client.get(f"{report_url}?priority=true").text
    assert 'href="/ui/reports"' in client.get(report_url).text
    assert "Original English evidence." not in client.get(report_url).text

    assert client.post("/api/v1/report-translations", json={"priority_run_id": run_id}).status_code == 202

    async def finish():
        if hub.report_translations._worker:
            await asyncio.wait_for(hub.report_translations._worker, timeout=3)

    client.portal.call(finish)
    page = client.get(report_url).text
    assert "中文依据 &lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "确定性判题结果：答案错误（WA）" in page
    assert all("deterministic_judge_verdict=WA" not in batch.values() for batch in calls)
    assert "算法 Critic" in page and "诊断依据" in page
    assert "缺乏支持" in page and "证明存在缺口" in page
    assert "Original English" not in page and "<pre>" not in page
    assert "<script>alert(1)</script>" not in page
    assert "已格式化" in page and "已格式化" in client.get("/ui/reports").text
    assert "中文化" not in page
    assert 'id="report-activity-spinner"' in page
    assert hub.store.get_run(run_id)["result"] == result
    count = len(calls)
    client.post("/api/v1/report-translations", json={"priority_run_id": run_id})
    client.portal.call(finish)
    assert len(calls) == count


def test_report_failure_page_has_back_button_and_never_starts_translation(client):
    hub = client.app.state.hub
    run = hub.store.create_run("road", {"problem_id": "road"})
    hub.store.update_run(run["run_id"], "FAILED", result={"error_code": "HY3_INVALID_RESPONSE"})
    response = client.get(f'/api/v1/runs/{run["run_id"]}/report')
    assert response.status_code == 200
    assert "报告为空" in response.text and "返回报告列表" in response.text
    assert "/static/reports.js" not in response.text
    assert hub.report_translations._worker is None


def test_report_client_uses_dedicated_options_without_mutating_solver(settings):
    settings.hy3.reasoning_effort = "high"
    settings.hy3.max_tokens = 127000
    settings.hy3.api_key = "mock-key"
    hub = create_app(settings).state.hub
    assert hub.model.settings is settings.hy3
    assert hub.model.settings.reasoning_effort == "high" and hub.model.settings.max_tokens == 127000
    report_model = hub.report_translations.model
    assert report_model.settings is not settings.hy3
    assert report_model.settings.api_key == settings.hy3.api_key
    assert report_model.settings.reasoning_effort == "low" and report_model.settings.max_tokens == 4096
    assert report_model.settings.max_attempts == 1
    assert report_model.timeout_seconds == 60
    assert hub.report_translations._worker is None
