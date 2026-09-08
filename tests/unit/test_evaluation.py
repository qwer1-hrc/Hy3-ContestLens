from hy3_contestlens.domain import CheckResult, CriticReview, ErrorType, StepAssessment, StepVerdict, TestResult as JudgeTestResult, Verdict
from hy3_contestlens.evaluation import (
    adjudicate,
    code_review_conflicts_with_compile,
    critic_review_quality_issues,
    repair_quality_gate,
)


def review(name, verdict=StepVerdict.SUPPORTED, error=ErrorType.UNRESOLVED):
    return CriticReview(reviewer=name, assessments=[StepAssessment(step_id="S1", verdict=verdict, evidence=["e"], confidence=0.9)], error_type=error, summary="review")


def test_correct_result_with_invalid_process_is_detected():
    check = CheckResult(check_id="check_x", verdict=Verdict.AC, problem_id="road", dataset_id="noip2018", passed=10, total=10, score=100, tests=[])
    diagnosis = adjudicate(review("algorithm_critic", StepVerdict.CONTRADICTED, ErrorType.PROOF_GAP), review("code_critic"), Verdict.OK, check)
    assert diagnosis.final_result_correct is True
    assert diagnosis.process_correct is False
    assert diagnosis.error_type == ErrorType.RESULT_CORRECT_PROCESS_INVALID
    assert diagnosis.first_error_step_id == "S1"


def test_local_compile_error_overrides_conflicting_code_review():
    algorithm = review("algorithm_critic")
    code = review("code_critic", error=ErrorType.RUNTIME_ERROR)

    diagnosis = adjudicate(algorithm, code, Verdict.CE, None)

    assert diagnosis.error_type == ErrorType.COMPILE_ERROR
    assert code_review_conflicts_with_compile(code, Verdict.CE) is True


def test_compile_conflict_detection_handles_success_and_matching_ce():
    compile_error = review("code_critic", error=ErrorType.COMPILE_ERROR)
    clean = review("code_critic")

    assert code_review_conflicts_with_compile(compile_error, Verdict.CE) is False
    assert code_review_conflicts_with_compile(compile_error, Verdict.OK) is True
    assert code_review_conflicts_with_compile(clean, Verdict.OK) is False


def test_empty_or_internally_inconsistent_critic_cannot_mark_process_correct():
    empty = CriticReview(reviewer="algorithm_critic", summary="claims a defect but has no assessments")
    contradictory = CriticReview(
        reviewer="code_critic",
        assessments=[StepAssessment(step_id="S1", verdict=StepVerdict.CONTRADICTED, evidence=["bad loop"], confidence=1)],
        error_type=ErrorType.UNRESOLVED,
        summary="defect",
    )
    check = CheckResult(
        check_id="check_x", verdict=Verdict.AC, problem_id="road", dataset_id="noip2018",
        passed=1, total=1, score=100, tests=[],
    )

    diagnosis = adjudicate(empty, contradictory, Verdict.OK, check)

    assert diagnosis.process_correct is False
    assert diagnosis.error_type == ErrorType.RESULT_CORRECT_PROCESS_INVALID
    assert "missing_structured_assessments" in critic_review_quality_issues(empty)
    assert "negative_assessment_with_unresolved_error_type" in critic_review_quality_issues(contradictory)
    assert "missing_first_error_step_id" in critic_review_quality_issues(contradictory)


def _test_result(test_id: str, verdict: Verdict) -> JudgeTestResult:
    return JudgeTestResult(
        test_id=test_id, verdict=verdict, memory_limit_mb=512,
        memory_limit_source="problem_manifest", expected_sha256="hash",
    )


def test_repair_quality_gate_blocks_regression_of_previously_passing_test():
    previous = CheckResult(
        check_id="before", verdict=Verdict.WA, problem_id="game", dataset_id="noip2018",
        passed=1, total=2, score=50,
        tests=[_test_result("game1", Verdict.WA), _test_result("game2", Verdict.AC)],
    )
    candidate = CheckResult(
        check_id="after", verdict=Verdict.WA, problem_id="game", dataset_id="noip2018",
        passed=1, total=2, score=50,
        tests=[_test_result("game1", Verdict.AC), _test_result("game2", Verdict.RE)],
    )

    gate = repair_quality_gate(previous, candidate)

    assert gate["passed"] is False
    assert gate["fixed_tests"] == ["game1"]
    assert gate["regressed_tests"] == ["game2"]
