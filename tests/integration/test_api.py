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
    assert "非腾讯官方发布" in page.text


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
