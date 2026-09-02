from pathlib import Path
import sqlite3

import pytest

from hy3_contestlens.errors import ContestLensError
from hy3_contestlens.store import Store


def test_existing_database_gets_additive_run_lease_migration(tmp_path: Path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE runs (run_id TEXT PRIMARY KEY, problem_id TEXT NOT NULL, status TEXT NOT NULL, "
            "request_json TEXT NOT NULL, result_json TEXT, cancelled INTEGER NOT NULL DEFAULT 0, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
    Store(path)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
    assert {"runner_id", "lease_expires_at", "attempt"} <= columns


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


def test_run_lease_is_exclusive_expires_and_preserves_restart_intent(tmp_path: Path):
    store = Store(tmp_path / "store.sqlite3")
    run_id = store.create_run("road", {"problem_id": "road"})["run_id"]
    store.queue_run(run_id)

    assert store.claim_run(run_id, "runner_a", lease_seconds=60)
    assert not store.claim_run(run_id, "runner_b", lease_seconds=60)
    assert store.list_recoverable_runs() == []

    # A negative deadline simulates a worker that died without graceful shutdown.
    assert store.renew_run_lease(run_id, "runner_a", lease_seconds=-1)
    assert store.list_recoverable_runs() == [run_id]
    assert store.claim_run(run_id, "runner_b", lease_seconds=60)
    assert store.get_run(run_id)["attempt"] == 2

    assert store.interrupt_run(run_id, reason="test_shutdown")
    assert store.get_run(run_id)["status"] == "INTERRUPTED"
    store.queue_run(run_id)
    assert store.get_run(run_id)["status"] == "QUEUED"


def test_late_worker_updates_cannot_revive_cancelled_or_completed_runs(tmp_path: Path):
    store = Store(tmp_path / "store.sqlite3")
    cancelled = store.create_run("road", {})["run_id"]
    store.queue_run(cancelled)
    store.cancel_run(cancelled)
    store.update_run(cancelled, "SOLVING")
    assert store.get_run(cancelled)["status"] == "CANCELLED"

    completed = store.create_run("road", {})["run_id"]
    store.update_run(completed, "COMPLETED", result={"stop_reason": "COMPLETED"})
    store.cancel_run(completed)
    assert store.get_run(completed)["status"] == "COMPLETED"
