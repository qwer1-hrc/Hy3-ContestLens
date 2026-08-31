from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .errors import ContestLensError
from .utils import canonical_json, safe_id, utc_now


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS resource_scopes (
  scope_id TEXT PRIMARY KEY,
  root_path TEXT NOT NULL,
  display_name TEXT NOT NULL,
  scope_kind TEXT NOT NULL,
  granted_by TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resource_bindings (
  binding_id TEXT PRIMARY KEY,
  problem_id TEXT NOT NULL UNIQUE,
  scope_id TEXT NOT NULL,
  data_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(scope_id) REFERENCES resource_scopes(scope_id)
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  problem_id TEXT NOT NULL,
  status TEXT NOT NULL,
  request_json TEXT NOT NULL,
  result_json TEXT,
  cancelled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  event_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(run_id, seq),
  FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS image_understanding (
  run_id TEXT PRIMARY KEY,
  state_json TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS idempotency (
  idempotency_key TEXT PRIMARY KEY,
  operation TEXT NOT NULL,
  response_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS annotations (
  task_id TEXT NOT NULL,
  label_id TEXT PRIMARY KEY,
  annotator TEXT NOT NULL,
  label_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS annotation_tasks (
  task_id TEXT PRIMARY KEY,
  task_json TEXT NOT NULL,
  adjudication_json TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS benchmarks (
  benchmark_id TEXT PRIMARY KEY,
  benchmark_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def ping(self) -> bool:
        with self.connect() as connection:
            return connection.execute("SELECT 1").fetchone()[0] == 1

    def put_scope(self, record: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO resource_scopes VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record["scope_id"], record["root_path"], record["display_name"],
                    record["scope_kind"], record["granted_by"], canonical_json(record["snapshot"]),
                    record["created_at"],
                ),
            )

    def get_scope(self, scope_id: str, *, trusted: bool = False) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM resource_scopes WHERE scope_id=?", (scope_id,)).fetchone()
        if not row:
            raise ContestLensError("RESOURCE_SCOPE_NOT_FOUND", "Resource scope does not exist", {"scope_id": scope_id}, 404)
        data = dict(row)
        data["snapshot"] = json.loads(data.pop("snapshot_json"))
        if not trusted:
            data.pop("root_path", None)
        return data

    def list_scopes(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT scope_id FROM resource_scopes ORDER BY created_at DESC").fetchall()
        return [self.get_scope(row[0]) for row in rows]

    def put_binding(self, problem_id: str, scope_id: str, data: dict[str, Any]) -> dict[str, Any]:
        binding_id = safe_id("binding")
        created_at = utc_now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO resource_bindings VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(problem_id) DO UPDATE SET binding_id=excluded.binding_id, scope_id=excluded.scope_id, data_json=excluded.data_json, created_at=excluded.created_at",
                (binding_id, problem_id, scope_id, canonical_json(data), created_at),
            )
        return {"binding_id": binding_id, "problem_id": problem_id, "scope_id": scope_id, **data, "created_at": created_at}

    def get_binding(self, problem_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM resource_bindings WHERE problem_id=?", (problem_id,)).fetchone()
        if not row:
            raise ContestLensError("RESOURCE_BINDING_NOT_FOUND", "No resource binding exists for this problem", {"problem_id": problem_id}, 404)
        return {
            "binding_id": row["binding_id"], "problem_id": row["problem_id"], "scope_id": row["scope_id"],
            **json.loads(row["data_json"]), "created_at": row["created_at"],
        }

    def create_run(self, problem_id: str, request: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        operation = "create_run"
        if idempotency_key:
            cached = self.idempotency_get(idempotency_key, operation)
            if cached:
                return cached
        run_id = safe_id("run")
        now = utc_now()
        response = {"run_id": run_id, "problem_id": problem_id, "status": "CREATED", "created_at": now}
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, NULL, 0, ?, ?)",
                (run_id, problem_id, "CREATED", canonical_json(request), now, now),
            )
        self.append_event(run_id, "CREATED", {"problem_id": problem_id})
        if idempotency_key:
            self.idempotency_put(idempotency_key, operation, response)
        return response

    def list_runs(self) -> list[dict[str, Any]]:
        """Return run history newest first, including results used to build reports."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT run_id, problem_id, status, created_at, updated_at, result_json "
                "FROM runs ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        records = []
        for row in rows:
            record = dict(row)
            result_json = record.pop("result_json")
            record["result"] = json.loads(result_json) if result_json else None
            records.append(record)
        return records

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            image_row = connection.execute("SELECT state_json FROM image_understanding WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise ContestLensError("RUN_NOT_FOUND", "Run does not exist", {"run_id": run_id}, 404)
        return {
            "run_id": row["run_id"], "problem_id": row["problem_id"], "status": row["status"],
            "request": json.loads(row["request_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "cancelled": bool(row["cancelled"]), "created_at": row["created_at"], "updated_at": row["updated_at"],
            "image_understanding": json.loads(image_row[0]) if image_row else None,
        }

    def put_image_understanding(self, run_id: str, data: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO image_understanding VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET state_json=excluded.state_json",
                (run_id, canonical_json(data)),
            )

    def decide_image_understanding(self, run_id: str, request_id: str, choice: str) -> dict[str, Any]:
        if choice not in {"use", "skip"}:
            raise ContestLensError("INVALID_IMAGE_CHOICE", "Choose use or skip")
        # Serialize competing clicks, timeout defaults and decisions from other app workers.
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute("SELECT status, cancelled FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run:
                raise ContestLensError("RUN_NOT_FOUND", "Run does not exist", status_code=404)
            row = connection.execute("SELECT state_json FROM image_understanding WHERE run_id=?", (run_id,)).fetchone()
            data = json.loads(row[0]) if row else {}
            if data.get("request_id") != request_id or run["cancelled"]:
                raise ContestLensError("IMAGE_CHOICE_EXPIRED", "Image choice is no longer active", status_code=409)
            if data.get("choice") == choice:
                return data
            if data.get("status") != "awaiting_choice" or data.get("choice") or run["status"] != "WAITING_FOR_IMAGE_CONFIRMATION":
                raise ContestLensError("IMAGE_CHOICE_EXPIRED", "Image choice is no longer active", status_code=409)
            data = {**data, "choice": choice, "decided_at": utc_now()}
            connection.execute("UPDATE image_understanding SET state_json=? WHERE run_id=?", (canonical_json(data), run_id))
        return data

    def update_run(self, run_id: str, status: str, *, result: dict[str, Any] | None = None, event: dict[str, Any] | None = None) -> None:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE runs SET status=?, result_json=COALESCE(?, result_json), updated_at=? WHERE run_id=?",
                (status, canonical_json(result) if result is not None else None, now, run_id),
            )
            if cursor.rowcount != 1:
                raise ContestLensError("RUN_NOT_FOUND", "Run does not exist", {"run_id": run_id}, 404)
        self.append_event(run_id, status, event or {})

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            cursor = connection.execute("UPDATE runs SET cancelled=1, status='CANCELLED', updated_at=? WHERE run_id=?", (utc_now(), run_id))
            if cursor.rowcount != 1:
                raise ContestLensError("RUN_NOT_FOUND", "Run does not exist", {"run_id": run_id}, 404)
        self.append_event(run_id, "CANCELLED", {})
        return self.get_run(run_id)

    def append_event(self, run_id: str, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self.connect() as connection:
            seq = connection.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
            event = {"seq": seq, "type": event_type, "data": data, "created_at": utc_now()}
            connection.execute("INSERT INTO events VALUES (?, ?, ?, ?)", (run_id, seq, canonical_json(event), event["created_at"]))
        return event

    def list_events(self, run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        self.get_run(run_id)
        with self.connect() as connection:
            rows = connection.execute("SELECT event_json FROM events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after_seq)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def idempotency_get(self, key: str, operation: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT response_json, operation FROM idempotency WHERE idempotency_key=?", (key,)).fetchone()
        if not row:
            return None
        if row["operation"] != operation:
            raise ContestLensError("IDEMPOTENCY_KEY_REUSED", "Idempotency key was used for another operation", status_code=409)
        return json.loads(row["response_json"])

    def idempotency_put(self, key: str, operation: str, response: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute("INSERT INTO idempotency VALUES (?, ?, ?, ?)", (key, operation, canonical_json(response), utc_now()))

    def create_annotation_task(self, data: dict[str, Any]) -> dict[str, Any]:
        task_id = safe_id("annotation")
        created_at = utc_now()
        record = {"task_id": task_id, **data, "created_at": created_at}
        with self.connect() as connection:
            connection.execute("INSERT INTO annotation_tasks VALUES (?, ?, NULL, ?)", (task_id, canonical_json(record), created_at))
        return record

    def get_annotation_task(self, task_id: str, *, reveal_prediction: bool = False) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM annotation_tasks WHERE task_id=?", (task_id,)).fetchone()
            labels = connection.execute("SELECT label_json FROM annotations WHERE task_id=? ORDER BY created_at", (task_id,)).fetchall()
        if not row:
            raise ContestLensError("ANNOTATION_TASK_NOT_FOUND", "Annotation task does not exist", {"task_id": task_id}, 404)
        task = json.loads(row["task_json"])
        if not reveal_prediction and not labels:
            task.pop("system_prediction", None)
        task["labels"] = [json.loads(item[0]) for item in labels]
        task["adjudication"] = json.loads(row["adjudication_json"]) if row["adjudication_json"] else None
        return task

    def add_annotation_label(self, task_id: str, annotator: str, label: dict[str, Any]) -> dict[str, Any]:
        self.get_annotation_task(task_id)
        label_id = safe_id("label")
        record = {"label_id": label_id, "task_id": task_id, "annotator": annotator, **label, "created_at": utc_now()}
        with self.connect() as connection:
            connection.execute("INSERT INTO annotations VALUES (?, ?, ?, ?, ?)", (task_id, label_id, annotator, canonical_json(record), record["created_at"]))
        return record

    def adjudicate_annotation(self, task_id: str, data: dict[str, Any]) -> dict[str, Any]:
        task = self.get_annotation_task(task_id, reveal_prediction=True)
        ensure_two = len(task["labels"]) >= 2
        if not ensure_two:
            raise ContestLensError("ADJUDICATION_REQUIRES_TWO_LABELS", "Two original blind labels are required before adjudication", status_code=409)
        record = {"task_id": task_id, **data, "created_at": utc_now()}
        with self.connect() as connection:
            connection.execute("UPDATE annotation_tasks SET adjudication_json=? WHERE task_id=?", (canonical_json(record), task_id))
        return record

    def export_annotations(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT task_id FROM annotation_tasks ORDER BY created_at").fetchall()
        return [self.get_annotation_task(row[0], reveal_prediction=True) for row in rows]

    def create_benchmark(self, data: dict[str, Any]) -> dict[str, Any]:
        benchmark_id = safe_id("benchmark")
        record = {"benchmark_id": benchmark_id, **data, "created_at": utc_now()}
        with self.connect() as connection:
            connection.execute("INSERT INTO benchmarks VALUES (?, ?, ?)", (benchmark_id, canonical_json(record), record["created_at"]))
        return record

    def get_benchmark(self, benchmark_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT benchmark_json FROM benchmarks WHERE benchmark_id=?", (benchmark_id,)).fetchone()
        if not row:
            raise ContestLensError("BENCHMARK_NOT_FOUND", "Benchmark does not exist", {"benchmark_id": benchmark_id}, 404)
        return json.loads(row[0])
