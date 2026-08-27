from __future__ import annotations

import json
from pathlib import Path

from hy3_contestlens.domain import CheckResult, CompileResult, CriticReview, Diagnosis, ProblemManifest, SolverOutput


PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / "docs" / "schemas"
SCHEMAS = {
    "problem_manifest": ProblemManifest,
    "solver_output": SolverOutput,
    "critic_review": CriticReview,
    "diagnosis": Diagnosis,
    "compile_result": CompileResult,
    "check_result": CheckResult,
}


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, model in SCHEMAS.items():
        (OUTPUT / f"{name}.schema.json").write_text(json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"schemas": sorted(SCHEMAS), "output": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

