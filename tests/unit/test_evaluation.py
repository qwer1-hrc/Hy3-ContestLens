from hy3_contestlens.domain import CheckResult, CriticReview, ErrorType, StepAssessment, StepVerdict, Verdict
from hy3_contestlens.evaluation import adjudicate, code_review_conflicts_with_compile


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
