from __future__ import annotations

import asyncio
from typing import Any

from hy3_contestlens.domain import (
    CheckResult,
    CompileResult,
    CriticReview,
    ErrorType,
    SolverOutput,
    StepAssessment,
    StepVerdict,
    TestResult as JudgeTestResult,
    Verdict,
)
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
    negative = error_type != ErrorType.UNRESOLVED
    return CriticReview(
        reviewer="code_critic",
        assessments=[StepAssessment(
            step_id="S1",
            verdict=StepVerdict.CONTRADICTED if negative else StepVerdict.SUPPORTED,
            evidence=["code evidence"],
            confidence=0.9,
        )],
        error_type=error_type,
        first_error_step_id="S1" if negative else None,
        code_location="main" if negative else None,
        summary="review",
    )


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


def test_runtime_recheck_and_repair_receive_privacy_safe_signal_evidence():
    client = CapturingClient(review(ErrorType.RUNTIME_ERROR))
    judge = {
        "verdict": "RE",
        "passed": 1,
        "total": 2,
        "tests": [
            {
                "test_id": "game14",
                "verdict": "RE",
                "cpu_ms": 17,
                "wall_ms": 61,
                "peak_rss_mb": 1.8,
                "memory_limit_mb": 512,
                "exit_code": 139,
                "termination_signal": 11,
                "timed_out": False,
                "memory_limited": False,
                "output_limited": False,
                "stdout_bytes": 0,
                "file_output_bytes": 0,
                "stderr_bytes": 0,
                "first_diff": {"line": 1, "column": 1, "expected_length": 7, "actual_length": 0, "actual_excerpt": "private"},
            }
        ],
    }

    asyncio.run(client.code_review(
        {"summary": "sample"}, solution(), "sample",
        compile_evidence={"verdict": "OK"}, judge_evidence=judge,
    ))

    prompt = client.calls[0]["prompt"]
    assert "DETERMINISTIC JUDGE EVIDENCE" in prompt
    assert '"exit_code": 139' in prompt
    assert '"termination_signal_name": "SIGSEGV"' in prompt
    assert '"actual_output_empty": true' in prompt
    assert "private" not in prompt
    assert "work queue" in prompt

    repair_client = CapturingClient(solution())
    asyncio.run(repair_client.repair(
        {"summary": "sample"}, solution(), {"error_type": "RUNTIME_ERROR"}, judge, 1, "sample",
        compile_summary={"verdict": "OK"},
    ))
    repair_prompt = repair_client.calls[0]["prompt"]
    assert "PRIVACY-SAFE DETERMINISTIC JUDGE EVIDENCE" in repair_prompt
    assert '"exit_code": 139' in repair_prompt
    assert '"termination_signal_name": "SIGSEGV"' in repair_prompt
    assert "private" not in repair_prompt
    assert "prove termination" in repair_prompt


class RecheckModel:
    def __init__(self):
        self.compile_evidence: dict[str, Any] | None = None

    async def code_review(
        self,
        problem_spec: dict[str, Any],
        candidate: SolverOutput,
        io_basename: str,
        compile_evidence: dict[str, Any] | None = None,
        judge_evidence: dict[str, Any] | None = None,
    ) -> CriticReview:
        self.compile_evidence = compile_evidence
        self.judge_evidence = judge_evidence
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
        workflow._recheck_code_review_if_needed(
            {"summary": "sample"}, solution(), "sample", initial, compile_result
        )
    )

    assert superseded is initial
    assert rechecked.error_type == ErrorType.COMPILE_ERROR
    assert model.compile_evidence == compile_result.model_dump(mode="json")


def test_workflow_rechecks_non_ac_code_review_with_runtime_evidence():
    model = RecheckModel()
    workflow = ContestWorkflow(None, None, None, None, model, None)
    initial = review(ErrorType.UNRESOLVED)
    compile_result = CompileResult(verdict=Verdict.OK, source_artifact_id="source_1")
    check = CheckResult(
        check_id="check_1", verdict=Verdict.RE, problem_id="game", dataset_id="noip2018",
        passed=0, total=1, score=0,
        tests=[JudgeTestResult(
            test_id="game14", verdict=Verdict.RE, memory_limit_mb=512,
            memory_limit_source="problem_manifest", expected_sha256="hash",
            exit_code=139, termination_signal=11,
        )],
    )

    rechecked, superseded = asyncio.run(
        workflow._recheck_code_review_if_needed(
            {"summary": "sample"}, solution(), "sample", initial, compile_result, check
        )
    )

    assert superseded is initial
    assert rechecked.error_type == ErrorType.COMPILE_ERROR
    assert model.judge_evidence == check.model_dump(mode="json")
