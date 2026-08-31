from pathlib import Path

import pytest

from hy3_contestlens.errors import ContestLensError
from hy3_contestlens.store import Store


def test_run_events_are_monotonic_and_idempotent(tmp_path: Path):
    store = Store(tmp_path / "store.sqlite3")
    first = store.create_run("road", {"problem_id": "road"}, idempotency_key="same")
    second = store.create_run("road", {"problem_id": "road"}, idempotency_key="same")
    assert first["run_id"] == second["run_id"]
    store.update_run(first["run_id"], "ANALYZING")
    events = store.list_events(first["run_id"])
    assert [item["seq"] for item in events] == [1, 2]


def test_idempotency_key_cannot_cross_operations(tmp_path: Path):
    store = Store(tmp_path / "store.sqlite3")
    store.idempotency_put("key", "x", {"ok": True})
    with pytest.raises(ContestLensError) as error:
        store.idempotency_get("key", "y")
    assert error.value.code == "IDEMPOTENCY_KEY_REUSED"


def test_run_history_includes_all_states_and_is_stable_for_equal_timestamps(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "store.sqlite3")
    assert store.list_runs() == []
    monkeypatch.setattr("hy3_contestlens.store.utc_now", lambda: "2026-08-29T16:00:00+00:00")
    first = store.create_run("road", {"problem_id": "road"})
    second = store.create_run("money", {"problem_id": "money"})
    failure = {"error_code": "HY3_NOT_CONFIGURED"}
    store.update_run(second["run_id"], "FAILED", result=failure)

    history = store.list_runs()
    assert [item["run_id"] for item in history] == [second["run_id"], first["run_id"]]
    assert history[0]["status"] == "FAILED"
    assert history[0]["result"] == failure
    assert history[1]["result"] is None
    assert "request_json" not in history[0] and "result_json" not in history[0]
