import pytest

from hy3_contestlens.reporting import has_evaluation_report, render_run_report


def test_failure_report_is_renderable_and_not_counted_as_model_result():
    rendered = render_run_report({"error_code": "HY3_NOT_CONFIGURED", "message": "missing", "details": None})
    assert "HY3_NOT_CONFIGURED" in rendered
    assert "不会计入模型准确率" in rendered


@pytest.mark.parametrize("result", [
    None, {}, {"error_code": "HY3_NOT_CONFIGURED"},
    {"initial_submission_result": None},
    {
        "run_id": "run_1", "problem_id": "road", "stop_reason": "COMPLETED",
        "initial_submission_result": {"diagnosis": {}}, "best_submission_result": {},
    },
])
def test_failure_or_incomplete_result_has_no_evaluation_report(result):
    assert not has_evaluation_report(result)


def test_complete_evaluation_without_test_points_still_has_a_report():
    evaluation = {"check": None, "diagnosis": {"process_correct": False, "error_type": "COMPILE_ERROR"}}
    result = {
        "run_id": "run_1", "problem_id": "road", "stop_reason": "REPAIR_DISABLED",
        "initial_submission_result": evaluation, "best_submission_result": evaluation,
    }
    assert has_evaluation_report(result)
    assert "Hy3-ContestLens 评测报告" in render_run_report(result)
