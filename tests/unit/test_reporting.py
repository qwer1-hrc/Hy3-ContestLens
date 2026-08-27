from hy3_contestlens.reporting import render_run_report


def test_failure_report_is_renderable_and_not_counted_as_model_result():
    rendered = render_run_report({"error_code": "HY3_NOT_CONFIGURED", "message": "missing", "details": None})
    assert "HY3_NOT_CONFIGURED" in rendered
    assert "不会计入模型准确率" in rendered

