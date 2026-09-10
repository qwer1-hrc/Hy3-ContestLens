from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

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


def critic_review_quality_issues(
    review: CriticReview,
    expected_step_ids: Iterable[str] | None = None,
) -> list[str]:
    """Return structural contradictions that make a model review unauditable."""
    issues: list[str] = []
    if not review.assessments:
        issues.append("missing_structured_assessments")
        return issues
    step_ids = [item.step_id for item in review.assessments]
    if any(not item.evidence or not all(text.strip() for text in item.evidence) for item in review.assessments):
        issues.append("missing_step_evidence")
    if len(step_ids) != len(set(step_ids)):
        issues.append("duplicate_step_assessments")
    if expected_step_ids is not None:
        expected = set(expected_step_ids)
        actual = set(step_ids)
        if expected - actual:
            issues.append("missing_step_assessments:" + ",".join(sorted(expected - actual)))
        if actual - expected:
            issues.append("unknown_step_assessments:" + ",".join(sorted(actual - expected)))
    negative = [
        item for item in review.assessments
        if item.verdict in {StepVerdict.CONTRADICTED, StepVerdict.UNSUPPORTED}
    ]
    if negative and review.error_type == ErrorType.UNRESOLVED:
        issues.append("negative_assessment_with_unresolved_error_type")
    if not negative and review.error_type != ErrorType.UNRESOLVED:
        issues.append("error_type_without_negative_assessment")
    negative_ids = {item.step_id for item in negative}
    if negative and review.first_error_step_id is None:
        issues.append("missing_first_error_step_id")
    elif review.first_error_step_id is not None and review.first_error_step_id not in negative_ids:
        issues.append("first_error_step_is_not_negative")
    if review.reviewer == "code_critic" and negative and not review.code_location:
        issues.append("missing_code_location")
    return issues


def _judge_observations(check: CheckResult | None) -> list[str]:
    if check is None:
        return []
    verdicts = Counter(item.verdict.value for item in check.tests)
    exits = Counter(str(item.exit_code) for item in check.tests if item.exit_code is not None)
    signals: Counter[str] = Counter()
    for item in check.tests:
        signal = item.termination_signal
        if signal is None and item.exit_code is not None:
            if item.exit_code < 0:
                signal = -item.exit_code
            elif 128 < item.exit_code <= 192:
                signal = item.exit_code - 128
        if signal is not None:
            signals[str(signal)] += 1
    evidence = [
        "judge_failure_pattern=" + ",".join(f"{key}:{value}" for key, value in sorted(verdicts.items())),
    ]
    if exits:
        evidence.append("judge_exit_codes=" + ",".join(f"{key}:{value}" for key, value in sorted(exits.items())))
    if signals:
        evidence.append("judge_termination_signals=" + ",".join(f"{key}:{value}" for key, value in sorted(signals.items())))
    flag_values = {
        "timed_out": [item.timed_out for item in check.tests],
        "memory_limited": [item.memory_limited for item in check.tests],
        "output_limited": [item.output_limited for item in check.tests],
    }
    flag_summary = ",".join(
        f"{name}:{sum(value is True for value in values)}/{sum(value is not None for value in values)}"
        for name, values in flag_values.items()
    )
    output_known = []
    for item in check.tests:
        if item.stdout_bytes is not None and item.file_output_bytes is not None:
            output_known.append(item.stdout_bytes == 0 and item.file_output_bytes == 0)
        elif isinstance(item.first_diff, dict) and isinstance(item.first_diff.get("actual_length"), int):
            output_known.append(item.first_diff["actual_length"] == 0)
    evidence.append(
        f"judge_flags={flag_summary},empty_output:{sum(output_known)}/{len(output_known)}"
    )
    return evidence


def adjudicate(
    algorithm: CriticReview,
    code: CriticReview,
    compile_verdict: Verdict,
    check: CheckResult | None,
    expected_step_ids: Iterable[str] | None = None,
) -> Diagnosis:
    judge_verdict = compile_verdict if compile_verdict != Verdict.OK else check.verdict if check else Verdict.SANDBOX_UNAVAILABLE
    final_correct = judge_verdict == Verdict.AC
    expected_steps = tuple(expected_step_ids) if expected_step_ids is not None else None
    contradicted = []
    for review in (algorithm, code):
        contradicted.extend(
            item for item in review.assessments
            if item.verdict in {StepVerdict.CONTRADICTED, StepVerdict.UNSUPPORTED}
        )
    quality_issues = {
        review.reviewer: critic_review_quality_issues(review, expected_steps) for review in (algorithm, code)
    }
    all_assessed = all(
        review.assessments and all(item.verdict == StepVerdict.SUPPORTED for item in review.assessments)
        for review in (algorithm, code)
    )
    process_correct = (
        all_assessed
        and not any(quality_issues.values())
        and algorithm.error_type == ErrorType.UNRESOLVED
        and code.error_type == ErrorType.UNRESOLVED
    )
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
            if assessment.verdict in {StepVerdict.CONTRADICTED, StepVerdict.UNSUPPORTED}:
                if first is None:
                    first = assessment.step_id
                evidence.extend(assessment.evidence)
                confidence_values.append(assessment.confidence)
        if review.summary:
            evidence.append(f"{review.reviewer}: {review.summary}")
        if quality_issues[review.reviewer]:
            evidence.append(
                f"critic_quality_gate[{review.reviewer}]=" + ",".join(quality_issues[review.reviewer])
            )
    if judge_verdict not in {Verdict.AC, Verdict.OK}:
        evidence.insert(0, f"deterministic_judge_verdict={judge_verdict.value}")
        confidence_values.append(1.0)
    evidence[1:1] = _judge_observations(check)
    confidence = sum(confidence_values) / len(confidence_values) if confidence_values else (0.9 if final_correct else 0.3)
    code_location = code.code_location or algorithm.code_location
    suggestion = repair_route(judge_verdict, error_type)
    triage = failure_triage(check)
    if triage["focus"] in {"WA", "RE", "IO_CONFLICT", "OLE"}:
        suggestion = ("Correctness first: reproduce public/synthetic counterexamples and repair invalid results, crashes or I/O. "
                      "Require public samples and small-case checks to pass before optimizing TLE or MLE. "
                      f"Handle each category separately: {triage['counts']}.")
    elif triage["focus"] == "MLE":
        suggestion = "Resolve memory exhaustion while preserving correct outputs; distinguish allocation growth from runtime errors, then optimize time."
    evidence.append("repair_priority=" + ",".join(triage["priority"]))
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
    if error_type in {ErrorType.STATE_TRANSITION_ERROR, ErrorType.RUNTIME_ERROR}:
        return "Patch the earliest proven control-flow, state-management, bounds, or lifetime defect; prove loop/work-queue termination, then run all tests and audit adjacent code for the same failure class."
    if verdict == Verdict.TLE or error_type == ErrorType.COMPLEXITY_TLE:
        return "First prove forward progress and termination of every loop/work queue and rule out blocking I/O; only then optimize the measured hot path, run all tests, and re-check the claimed complexity."
    if verdict == Verdict.MLE or error_type == ErrorType.COMPLEXITY_MLE:
        return "Reduce state or data-structure memory, then run all tests under the problem memory limit."
    if verdict in {Verdict.RE, Verdict.OLE, Verdict.IO_CONFLICT}:
        return "Apply a focused implementation or I/O patch, then compile and run every test."
    if error_type == ErrorType.RESULT_CORRECT_PROCESS_INVALID:
        return "Repair the proof, dependencies, or code-step mapping; rejudge only if source code changes."
    return "Repair the earliest contradicted step and its mapped implementation, then compile, run all tests, and repeat process reviews."


def repair_quality_gate(previous: CheckResult | None, candidate: CheckResult | None) -> dict[str, Any]:
    """Describe deterministic per-test repair regressions without exposing private data."""
    previous_tests = {item.test_id: item for item in previous.tests} if previous else {}
    candidate_tests = {item.test_id: item for item in candidate.tests} if candidate else {}
    fixed = sorted(
        test_id for test_id, item in previous_tests.items()
        if item.verdict != Verdict.AC
        and candidate_tests.get(test_id) is not None
        and candidate_tests[test_id].verdict == Verdict.AC
    )
    regressed = sorted(
        test_id for test_id, item in previous_tests.items()
        if item.verdict == Verdict.AC
        and (candidate_tests.get(test_id) is None or candidate_tests[test_id].verdict != Verdict.AC)
    )
    changed = [
        {
            "test_id": test_id,
            "before": previous_tests[test_id].verdict.value,
            "after": candidate_tests[test_id].verdict.value,
        }
        for test_id in sorted(previous_tests)
        if previous_tests[test_id].verdict != Verdict.AC
        and candidate_tests.get(test_id) is not None
        and candidate_tests[test_id].verdict not in {Verdict.AC, previous_tests[test_id].verdict}
    ]
    return {
        "passed": not regressed,
        "fixed_tests": fixed,
        "regressed_tests": regressed,
        "changed_failure_modes": changed,
    }


def failure_triage(check: CheckResult | None) -> dict[str, Any]:
    """Keep mixed failures visible; an aggregate timeout must not hide wrong answers."""
    counts = Counter(t.verdict.value for t in check.tests) if check else Counter()
    if check and not counts:
        counts[check.verdict.value] = 1
    pending = [v for v in ("RE", "WA", "IO_CONFLICT", "OLE", "MLE", "TLE") if counts[v]]
    return {"counts": dict(counts), "priority": pending, "focus": pending[0] if pending else None}


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
