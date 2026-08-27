from __future__ import annotations

import json

from hy3_contestlens.service import ServiceHub


def main() -> None:
    hub = ServiceHub()
    scopes = hub.store.list_scopes()
    if not scopes:
        raise SystemExit("No configured resource root is available")
    scope_id = scopes[0]["scope_id"]
    bindings = []
    for manifest in hub.catalog.list():
        found = hub.resources.find_problem_assets(scope_id, manifest.problem_id, manifest.title_zh, manifest.io.basename)
        candidate_id = found.get("auto_selected_candidate_id")
        if not candidate_id:
            bindings.append({"problem_id": manifest.problem_id, "status": found["status"]})
            continue
        binding = hub.resources.bind_candidate(scope_id, manifest.problem_id, hub.resources.get_candidate(candidate_id), confirmed_by="configured_root_auto_bind")
        bindings.append({"problem_id": manifest.problem_id, "status": "BOUND", "binding_id": binding["binding_id"]})
    print(json.dumps(bindings, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

