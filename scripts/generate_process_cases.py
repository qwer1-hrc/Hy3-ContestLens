from __future__ import annotations

import json
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / "data" / "process_cases" / "noip2018_process_cases.jsonl"
PROBLEMS = ("road", "money", "track", "travel", "game", "defense")
SCENARIOS = (
    ("valid_reference", None, None, True, True),
    ("valid_alternative", None, None, True, True),
    ("valid_boundary_explicit", None, None, True, True),
    ("statement_misread", "STATEMENT_MISREAD", "S1", False, False),
    ("constraint_omission", "CONSTRAINT_OMISSION", "S2", False, False),
    ("wrong_algorithm", "WRONG_ALGORITHM", "S2", False, False),
    ("proof_gap", "PROOF_GAP", "S3", True, False),
    ("complexity_tle", "COMPLEXITY_TLE", "S4", False, False),
    ("boundary_error", "BOUNDARY_ERROR", "S3", False, False),
    ("integer_overflow", "INTEGER_OVERFLOW", "S4", False, False),
    ("implementation_mismatch", "IMPLEMENTATION_MISMATCH", "S5", False, False),
    ("result_correct_process_invalid", "RESULT_CORRECT_PROCESS_INVALID", "S2", True, False),
)


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for problem in PROBLEMS:
        for index, (scenario, error_type, first_step, final_correct, process_correct) in enumerate(SCENARIOS, 1):
            records.append({
                "schema_version": 1, "case_id": f"{problem}_process_{index:02d}", "dataset_id": "noip2018",
                "problem_id": problem, "scenario": scenario, "source": "deterministic synthetic mutation plan",
                "construction": "Template defines the intended mutation; concrete Solver output and C++ must be materialized before a real validation run.",
                "difficulty": None, "expected_final_result_correct": final_correct,
                "expected_process_correct": process_correct, "expected_first_error_step_id": first_step,
                "expected_error_type": error_type, "validation_status": "TEMPLATE_NOT_EXECUTED",
            })
    OUTPUT.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records), encoding="utf-8")
    print(json.dumps({"path": str(OUTPUT), "count": len(records), "status": "templates_only"}, ensure_ascii=False))


if __name__ == "__main__":
    main()

