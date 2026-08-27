from __future__ import annotations

import argparse
import json
import time

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--problem", default="road")
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    client = httpx.Client(base_url=args.base_url, timeout=60)
    health = client.get("/healthz"); health.raise_for_status()
    problems = client.get("/api/v1/datasets/noip2018/problems"); problems.raise_for_status()
    assert len(problems.json()) == 6
    binding = client.get(f"/api/v1/problems/{args.problem}/resource-binding")
    binding.raise_for_status()
    created = client.post("/api/v1/runs", json={"problem_id": args.problem, "resource_binding_id": binding.json()["binding_id"], "repair": {"enabled": True, "max_rounds": 3}})
    created.raise_for_status(); run_id = created.json()["run_id"]
    started = client.post(f"/api/v1/runs/{run_id}/start"); started.raise_for_status()
    if args.wait:
        while True:
            state = client.get(f"/api/v1/runs/{run_id}").json()
            if state["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                print(json.dumps(state, ensure_ascii=False, indent=2)); break
            time.sleep(1)
    else:
        print(json.dumps({"run_id": run_id, "status": "started"}, ensure_ascii=False))


if __name__ == "__main__":
    main()

