from __future__ import annotations

import asyncio
from typing import Any

from hy3_contestlens.domain import CompileResult, CriticReview, ErrorType, SolverOutput, Verdict
from hy3_contestlens.model import Hy3Client
from hy3_contestlens.settings import Hy3Settings
from hy3_contestlens.workflow import ContestWorkflow


def solution() -> SolverOutput:
    return SolverOutput.model_validate(
        {
            "problem_summary": "sample",
            "steps": [
                {
                    "step_id": "S1",
                    "goal": "compile",
                    "statement": "Build the program.",
                    "justification": "The source must compile.",
                }
            ],
            "complexity": {"time": "O(1)", "space": "O(1)"},
            "cpp_source": "int main() { return 0; }",
        }
    )


def review(error_type: ErrorType) -> CriticReview:
    return CriticReview(reviewer="code_critic", error_type=error_type, summary="review")


class CapturingClient(Hy3Client):
    def __init__(self, response: Any):
        super().__init__(Hy3Settings(api_key="test-key"))
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def json_completion(self, *, role: str, prompt: str, schema: type[Any]) -> Any:
        self.calls.append({"role": role, "prompt": prompt, "schema": schema})
        return self.response


def test_code_recheck_uses_fresh_role_and_authoritative_diagnostics():
    client = CapturingClient(review(ErrorType.COMPILE_ERROR))
    diagnostic = "/source/main.cpp:8: error: invalid types 'int[int]' for array subscript"

    result = asyncio.run(
        client.code_review(
            {"summary": "sample"},
            solution(),
            "sample",
            compile_evidence={"verdict": "CE", "compiler": "g++", "diagnostics": [diagnostic]},
        )
    )

    assert result.error_type == ErrorType.COMPILE_ERROR
    assert client.calls[0]["role"] == "code_critic_recheck"
    assert diagnostic in client.calls[0]["prompt"]
    assert "error_type must be COMPILE_ERROR" in client.calls[0]["prompt"]
    assert "Do not assume the prior review was correct" in client.calls[0]["prompt"]


def test_repair_prompt_contains_local_compile_diagnostics():
    repaired = solution()
    client = CapturingClient(repaired)
    diagnostics = ["main.cpp:4: error: missing ';'", "compilation terminated"]

    result = asyncio.run(
        client.repair(
            {"summary": "sample"},
            solution(),
            {"error_type": "COMPILE_ERROR"},
            {"verdict": "CE"},
            1,
            "sample",
            compile_summary={"verdict": "CE", "compiler": "g++", "diagnostics": diagnostics},
        )
    )

    assert result is repaired
    prompt = client.calls[0]["prompt"]
    assert "LOCAL COMPILATION EVIDENCE" in prompt
    assert all(item in prompt for item in diagnostics)


class RecheckModel:
    def __init__(self):
        self.compile_evidence: dict[str, Any] | None = None

    async def code_review(
        self,
        problem_spec: dict[str, Any],
        candidate: SolverOutput,
        io_basename: str,
        compile_evidence: dict[str, Any] | None = None,
    ) -> CriticReview:
        self.compile_evidence = compile_evidence
        return review(ErrorType.COMPILE_ERROR)


def test_workflow_restarts_code_review_when_local_ce_conflicts():
    model = RecheckModel()
    workflow = ContestWorkflow(None, None, None, None, model, None)
    initial = review(ErrorType.RUNTIME_ERROR)
    compile_result = CompileResult(
        verdict=Verdict.CE,
        source_artifact_id="source_1",
        compiler="g++",
        diagnostics=["main.cpp:8: error: invalid subscript"],
    )

    rechecked, superseded = asyncio.run(
        workflow._recheck_code_review_if_conflicting(
            {"summary": "sample"}, solution(), "sample", initial, compile_result
        )
    )

    assert superseded is initial
    assert rechecked.error_type == ErrorType.COMPILE_ERROR
    assert model.compile_evidence == compile_result.model_dump(mode="json")
