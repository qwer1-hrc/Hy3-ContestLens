from fastapi.testclient import TestClient

from hy3_contestlens.api.app import CreateRunRequest, create_app
from hy3_contestlens.settings import ImageUnderstandingSettings


def test_vision_capability_is_optional_and_keeps_secrets_private(settings):
    settings.image_understanding = ImageUnderstandingSettings(api_key="private-vision-key")
    client = TestClient(create_app(settings))
    response = client.get("/api/v1/system/capabilities")
    assert response.json()["image_understanding"]["configured"]
    assert "private-vision-key" not in response.text
    assert CreateRunRequest(problem_id="road").image_understanding == "skip"


def test_image_decision_contract_rejects_stale_conflicting_and_cancelled_choices(settings):
    client = TestClient(create_app(settings))
    store = client.app.state.hub.store
    run_id = store.create_run("road", {"image_understanding": "ask"})["run_id"]
    path = f"/api/v1/runs/{run_id}/image-understanding"
    payload = {"request_id": "image_choice_one", "choice": "use"}
    assert client.post(path, json=payload).status_code == 409
    store.put_image_understanding(run_id, {"request_id": "image_choice_one", "status": "awaiting_choice", "choice": None})
    store.update_run(run_id, "WAITING_FOR_IMAGE_CONFIRMATION")
    assert client.get(f"/api/v1/runs/{run_id}").json()["image_understanding"]["request_id"] == "image_choice_one"
    assert client.post(path, json=payload).status_code == 200
    assert client.post(path, json=payload).status_code == 200
    assert client.post(path, json={**payload, "choice": "skip"}).status_code == 409
    assert client.post(path, json={**payload, "request_id": "old"}).status_code == 409
    assert client.post(path, json={**payload, "choice": "unknown"}).status_code == 422
    store.cancel_run(run_id)
    assert client.post(path, json=payload).status_code == 409
    assert client.post("/api/v1/runs/missing/image-understanding", json=payload).status_code == 404
