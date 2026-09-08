from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from hy3_contestlens.settings import AppSettings


class Client:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        response = httpx.request(method, self.base_url + path, json=body, timeout=120)
        try:
            data = response.json()
        except ValueError:
            data = response.text
        if response.is_error:
            raise RuntimeError(json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data)
        return data


def parser() -> argparse.ArgumentParser:
    settings = AppSettings.load()
    client_host = "127.0.0.1" if settings.host in {"0.0.0.0", "::"} else settings.host
    default_base_url = f"http://{client_host}:{settings.port}"
    root = argparse.ArgumentParser(prog="hy3-contest", description="Hy3-ContestLens REST/automation client")
    root.add_argument("--base-url", default=default_base_url)
    root.add_argument("--json", action="store_true", dest="as_json")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("health")
    sub.add_parser("list-problems")
    show = sub.add_parser("show-problem"); show.add_argument("problem")
    resources = sub.add_parser("resources"); resources_sub = resources.add_subparsers(dest="resources_command", required=True)
    validate = resources_sub.add_parser("validate"); validate.add_argument("--path", required=True)
    grant = resources_sub.add_parser("grant"); grant.add_argument("--path", required=True)
    discover = resources_sub.add_parser("discover"); discover.add_argument("--scope", required=True); discover.add_argument("--problem", required=True)
    bind = resources_sub.add_parser("bind"); bind.add_argument("--scope", required=True); bind.add_argument("--problem", required=True); bind.add_argument("--candidate", required=True)
    solve = sub.add_parser("solve"); solve.add_argument("--problem", required=True); solve.add_argument("--max-repair-rounds", type=int, default=3)
    submit = sub.add_parser("submit"); submit.add_argument("--run", required=True); submit.add_argument("--problem", required=True); submit.add_argument("--source", type=Path, required=True)
    compile_cmd = sub.add_parser("compile"); compile_cmd.add_argument("--run", required=True); compile_cmd.add_argument("--submission", required=True); compile_cmd.add_argument("--revision", required=True)
    check = sub.add_parser("check"); check.add_argument("--compile-artifact", required=True); check.add_argument("--problem", required=True)
    watch = sub.add_parser("watch"); watch.add_argument("--run", required=True)
    report = sub.add_parser("report"); report.add_argument("--run", required=True); report.add_argument("--format", choices=["json", "html"], default="json")
    return root


def emit(value: Any, as_json: bool) -> None:
    if as_json or isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False, indent=None if as_json else 2))
    else:
        print(value)


def run(args: argparse.Namespace) -> Any:
    client = Client(args.base_url)
    if args.command == "health":
        return client.request("GET", "/readyz")
    if args.command == "list-problems":
        return client.request("GET", "/api/v1/problems")
    if args.command == "show-problem":
        return client.request("GET", f"/api/v1/problems/{args.problem}")
    if args.command == "resources":
        if args.resources_command == "validate":
            return client.request("POST", "/api/v1/resource-scopes:validate", {"path": args.path})
        if args.resources_command == "grant":
            return client.request("POST", "/api/v1/resource-scopes", {"path": args.path, "confirmed": True})
        if args.resources_command == "discover":
            return client.request("POST", f"/api/v1/resource-scopes/{args.scope}:discover", {"problem_id": args.problem, "mode": "auto", "search_tests": True})
        if args.resources_command == "bind":
            return client.request("POST", f"/api/v1/problems/{args.problem}/resource-binding", {"scope_id": args.scope, "candidate_id": args.candidate})
    if args.command == "solve":
        created = client.request("POST", "/api/v1/runs", {"problem_id": args.problem, "repair": {"enabled": True, "max_rounds": args.max_repair_rounds}})
        client.request("POST", f"/api/v1/runs/{created['run_id']}/start")
        return created
    if args.command == "submit":
        return client.request("POST", f"/api/v1/runs/{args.run}/submissions", {"problem_id": args.problem, "source_code": args.source.read_text(encoding="utf-8"), "created_by": "cli"})
    if args.command == "compile":
        revision = client.request("GET", f"/api/v1/runs/{args.run}/submissions/{args.submission}/revisions/{args.revision}")
        frozen = client.request("POST", f"/api/v1/runs/{args.run}/submissions/{args.submission}/revisions/{args.revision}:freeze", {"expected_sha256": revision["sha256"]})
        return client.request("POST", "/api/v1/judge/compile", {"problem_id": revision["problem_id"], "source_artifact_id": frozen["source_artifact_id"], "source_sha256": frozen["source_sha256"]})
    if args.command == "check":
        manifest = client.request("GET", f"/api/v1/problems/{args.problem}")
        return client.request("POST", "/api/v1/judge/check-answer", {"compile_artifact_id": args.compile_artifact, "dataset_id": manifest["dataset_id"], "problem_id": args.problem})
    if args.command == "watch":
        cursor = 0
        while True:
            events = client.request("GET", f"/api/v1/runs/{args.run}/events?after_seq={cursor}")
            for event in events["events"]:
                cursor = event["seq"]
                print(json.dumps(event, ensure_ascii=False))
            state = client.request("GET", f"/api/v1/runs/{args.run}")
            if state["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                return state
            time.sleep(1)
    if args.command == "report":
        path = f"/api/v1/runs/{args.run}/{'result' if args.format == 'json' else 'report'}"
        return client.request("GET", path)
    raise RuntimeError("Unsupported command")


def main() -> None:
    args = parser().parse_args()
    try:
        emit(run(args), args.as_json)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
