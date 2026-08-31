from fastapi.testclient import TestClient

from hy3_contestlens.api.app import create_app


def test_health_openapi_problem_and_webui(settings):
    client = TestClient(create_app(settings))
    assert client.get("/healthz").status_code == 200
    problems = client.get("/api/v1/datasets/noip2018/problems")
    assert problems.status_code == 200
    assert len(problems.json()) == 6
    assert all(item["luogu_difficulty"] is None for item in problems.json())
    assert client.get("/openapi.json").status_code == 200
    page = client.get("/")
    assert page.status_code == 200
    assert "非腾讯官方发布" not in page.text
    assert "<footer>" not in page.text
    assert 'target="_blank"' in page.text
    assert "新页面" in page.text

    created = client.app.state.hub.store.create_run(
        "road",
        {"problem_id": "road", "repair": {"enabled": True, "max_rounds": 3}},
    )
    run_page = client.get(f"/ui/runs/{created['run_id']}")
    assert run_page.status_code == 200
    assert "工作流进度" in run_page.text
    assert "测试点信息" in run_page.text
    assert 'id="judge-list"' in run_page.text
    assert 'id="run-activity-spinner"' in run_page.text


def test_webui_removes_the_four_explanatory_notes(settings):
    client = TestClient(create_app(settings))
    for path, removed in (
        ("/ui/resources", "路径校验不会自动授权"),
        ("/ui/runs/new", "运行开始后会冻结题面"),
        ("/ui/reports", "每次运行都保留在这里"),
        ("/", "个人 / 犀牛鸟活动作品"),
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert removed not in response.text
        assert "<footer>" not in response.text
    reports = client.get("/ui/reports").text
    assert "报告格式化" in reports
    assert "中文化" not in reports


def test_readiness_is_componentized(settings):
    client = TestClient(create_app(settings))
    response = client.get("/readyz")
    assert response.status_code in {200, 503}
    components = response.json()["components"]
    assert {"database", "hy3", "resources_mcp", "workspace_mcp", "judge_mcp", "docker", "private_dataset"} <= components.keys()
    assert "api_key" not in response.text.lower()


def test_openapi_contains_external_contracts(settings):
    client = TestClient(create_app(settings))
    paths = client.get("/openapi.json").json()["paths"]
    required = {
        "/api/v1/resource-scopes:validate", "/api/v1/runs", "/api/v1/judge/compile",
        "/api/v1/judge/check-answer", "/api/v1/benchmarks", "/api/v1/annotations/tasks",
    }
    assert required <= paths.keys()


def test_annotation_prediction_is_blind_until_a_label(settings):
    client = TestClient(create_app(settings))
    created = client.post("/api/v1/annotations/tasks", json={"case_id": "case_1", "problem_id": "road", "material": {"steps": []}, "system_prediction": {"error_type": "WRONG_ALGORITHM"}})
    assert created.status_code == 201
    task_id = created.json()["task_id"]
    hidden = client.get(f"/api/v1/annotations/tasks/{task_id}").json()
    assert "system_prediction" not in hidden
    label = {"annotator": "a", "final_result_correct": False, "process_correct": False, "first_error_step_id": "S2", "error_type": "WRONG_ALGORITHM", "notes": "blind"}
    assert client.post(f"/api/v1/annotations/tasks/{task_id}/labels", json=label).status_code == 201
    revealed = client.get(f"/api/v1/annotations/tasks/{task_id}").json()
    assert revealed["system_prediction"]["error_type"] == "WRONG_ALGORITHM"
