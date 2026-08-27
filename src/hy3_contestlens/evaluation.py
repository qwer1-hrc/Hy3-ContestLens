from __future__ import annotations

from collections import Counter
from typing import Iterable

from .domain import CheckResult, CriticReview, Diagnosis, ErrorType, StepVerdict, Verdict


JUDGE_ERROR_MAP = {
    Verdict.CE: ErrorType.COMPILE_ERROR,
    Verdict.TLE: ErrorType.COMPLEXITY_TLE,
    Verdict.MLE: ErrorType.COMPLEXITY_MLE,
    Verdict.RE: ErrorType.RUNTIME_ERROR,
    Verdict.OLE: ErrorType.IO_OR_FORMAT_ERROR,
    Verdict.IO_CONFLICT: ErrorType.IO_OR_FORMAT_ERROR,
    Verdict.WA: ErrorType.IMPLEMENTATION_MISMATCH,
}


def adjudicate(algorithm: CriticReview, code: CriticReview, compile_verdict: Verdict, check: CheckResult | None) -> Diagnosis:
    judge_verdict = compile_verdict if compile_verdict != Verdict.OK else check.verdict if check else Verdict.SANDBOX_UNAVAILABLE
    final_correct = judge_verdict == Verdict.AC
    contradicted = []
    for review in (algorithm, code):
        contradicted.extend(item for item in review.assessments if item.verdict == StepVerdict.CONTRADICTED)
    process_correct = not contradicted and algorithm.error_type == ErrorType.UNRESOLVED and code.error_type == ErrorType.UNRESOLVED
    if final_correct and not process_correct:
        error_type = ErrorType.RESULT_CORRECT_PROCESS_INVALID
    elif compile_verdict == Verdict.CE:
        # A local compiler result is deterministic evidence. A model review may
        # help localize the defect, but it must not override a confirmed CE.
        error_type = ErrorType.COMPILE_ERROR
    elif judge_verdict in JUDGE_ERROR_MAP:
        critic_types = [item.error_type for item in (algorithm, code) if item.error_type != ErrorType.UNRESOLVED]
        error_type = Counter(critic_types).most_common(1)[0][0] if critic_types else JUDGE_ERROR_MAP[judge_verdict]
    elif not process_correct:
        critic_types = [item.error_type for item in (algorithm, code) if item.error_type != ErrorType.UNRESOLVED]
        error_type = critic_types[0] if critic_types else ErrorType.PROOF_GAP
    else:
        error_type = ErrorType.UNRESOLVED
    first = None
    evidence: list[str] = []
    confidence_values: list[float] = []
    for review in (algorithm, code):
        if first is None and review.first_error_step_id:
            first = review.first_error_step_id
        for assessment in review.assessments:
            if assessment.verdict == StepVerdict.CONTRADICTED:
                if first is None:
                    first = assessment.step_id
                evidence.extend(assessment.evidence)
                confidence_values.append(assessment.confidence)
        if review.summary:
            evidence.append(f"{review.reviewer}: {review.summary}")
    if judge_verdict not in {Verdict.AC, Verdict.OK}:
        evidence.insert(0, f"deterministic_judge_verdict={judge_verdict.value}")
        confidence_values.append(1.0)
    confidence = sum(confidence_values) / len(confidence_values) if confidence_values else (0.9 if final_correct else 0.3)
    code_location = code.code_location or algorithm.code_location
    suggestion = repair_route(judge_verdict, error_type)
    return Diagnosis(
        error_type=error_type, first_error_step_id=first, code_location=code_location, evidence=evidence[:20],
        confidence=min(1.0, confidence), final_result_correct=final_correct, process_correct=process_correct,
        repair_suggestion=suggestion,
    )


def code_review_conflicts_with_compile(review: CriticReview, compile_verdict: Verdict) -> bool:
    """Return whether a code review contradicts deterministic compilation facts."""
    if compile_verdict == Verdict.CE:
        return review.error_type != ErrorType.COMPILE_ERROR
    if compile_verdict == Verdict.OK:
        return review.error_type == ErrorType.COMPILE_ERROR
    return False


def repair_route(verdict: Verdict, error_type: ErrorType) -> str | None:
    if error_type == ErrorType.UNRESOLVED:
        return None
    if verdict == Verdict.CE:
        return "Patch the compiler-diagnosed source error, then compile, run all tests, and repeat both process reviews."
    if verdict == Verdict.TLE or error_type == ErrorType.COMPLEXITY_TLE:
        return "Replace or optimize the algorithm, then run all tests and re-check the claimed complexity."
    if verdict == Verdict.MLE or error_type == ErrorType.COMPLEXITY_MLE:
        return "Reduce state or data-structure memory, then run all tests under the problem memory limit."
    if verdict in {Verdict.RE, Verdict.OLE, Verdict.IO_CONFLICT}:
        return "Apply a focused implementation or I/O patch, then compile and run every test."
    if error_type == ErrorType.RESULT_CORRECT_PROCESS_INVALID:
        return "Repair the proof, dependencies, or code-step mapping; rejudge only if source code changes."
    return "Repair the earliest contradicted step and its mapped implementation, then compile, run all tests, and repeat process reviews."


def is_complete(compile_verdict: Verdict, check: CheckResult | None, diagnosis: Diagnosis) -> bool:
    return compile_verdict == Verdict.OK and check is not None and check.verdict == Verdict.AC and diagnosis.process_correct


def improvement_key(compile_verdict: Verdict, check: CheckResult | None, diagnosis: Diagnosis) -> tuple[int, int, int, int]:
    compiled = int(compile_verdict == Verdict.OK)
    passed = check.passed if check else 0
    severity = 0
    if check:
        severity = {
            Verdict.RE: 0, Verdict.TLE: 1, Verdict.MLE: 1, Verdict.WA: 2, Verdict.IO_CONFLICT: 2, Verdict.OLE: 1, Verdict.AC: 3,
        }.get(check.verdict, -1)
    process = int(diagnosis.process_correct)
    return compiled, passed, severity, process


def localization_metrics(predictions: Iterable[dict], annotations: Iterable[dict]) -> dict:
    truth = {item["case_id"]: item for item in annotations}
    compared = [item for item in predictions if item.get("case_id") in truth]
    error_cases = [item for item in compared if not truth[item["case_id"]].get("final_result_correct", False)]
    correctly_localized = sum(
        item.get("first_error_step_id") == truth[item["case_id"]].get("first_error_step_id") for item in error_cases
    )
    correct_cases = [item for item in compared if truth[item["case_id"]].get("final_result_correct", False)]
    flagged = [item for item in correct_cases if not item.get("process_correct", True)]
    true_process_issues = sum(not truth[item["case_id"]].get("process_correct", True) for item in flagged)
    false_positives = len(flagged) - true_process_issues
    return {
        "localization_accuracy": correctly_localized / len(error_cases) if error_cases else None,
        "localization_n": len(error_cases),
        "flagged_correct_answer_cases": len(flagged),
        "true_process_issue_rate_among_flagged": true_process_issues / len(flagged) if flagged else None,
        "false_positive_rate_among_flagged": false_positives / len(flagged) if flagged else None,
    }
