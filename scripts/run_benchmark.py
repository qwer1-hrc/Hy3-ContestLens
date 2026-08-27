from __future__ import annotations

import argparse
import json
import time

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--problems", nargs="*", default=["road", "money", "track", "travel", "game", "defense"])
    parser.add_argument("--max-repair-rounds", type=int, default=3)
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    client = httpx.Client(base_url=args.base_url, timeout=60)
    response = client.post("/api/v1/benchmarks", json={"problem_ids": args.problems, "repair": {"enabled": True, "max_rounds": args.max_repair_rounds}})
    response.raise_for_status(); benchmark = response.json()
    if not args.wait:
        print(json.dumps(benchmark, ensure_ascii=False, indent=2)); return
    while True:
        state = client.get(f"/api/v1/benchmarks/{benchmark['benchmark_id']}"); state.raise_for_status()
        if state.json()["status"] == "COMPLETED":
            result = client.get(f"/api/v1/benchmarks/{benchmark['benchmark_id']}/results"); result.raise_for_status()
            print(json.dumps(result.json(), ensure_ascii=False, indent=2)); return
        time.sleep(2)


if __name__ == "__main__":
    main()

