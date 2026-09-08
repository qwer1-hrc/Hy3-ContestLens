import pytest
from types import SimpleNamespace

from hy3_contestlens.domain import CheckResult, CompileResult, TestResult as JudgeTestResult, Verdict
from hy3_contestlens.workflow import ContestWorkflow


class RecordingStore:
    def __init__(self):
        self.records = []

    def update_run(self, run_id, status, *, result=None, event=None):
        self.records.append((status, event or {}))

    def append_event(self, run_id, event_type, data):
        self.records.append((event_type, data))


class FrozenWorkspace:
    def freeze_cpp_revision(self, run_id, submission_id, revision_id, sha256):
        return {"source_artifact_id": "source_1", "source_sha256": sha256}


class SuccessfulJudge:
    def compile_cpp(self, problem_id, source_artifact_id, source_sha256):
        return CompileResult(
            verdict=Verdict.OK,
            compile_artifact_id="compile_1",
            source_artifact_id=source_artifact_id,
            duration_ms=18,
        )

    def check_answer(self, compile_artifact_id, dataset_id, problem_id):
        return CheckResult(
            check_id="check_1",
            verdict=Verdict.AC,
            problem_id=problem_id,
            dataset_id=dataset_id,
            passed=1,
            total=1,
            score=10,
            tests=[
                JudgeTestResult(
                    test_id="road_001",
                    verdict=Verdict.AC,
                    wall_ms=7,
                    peak_rss_mb=3.5,
                    memory_limit_mb=512,
                    memory_limit_source="default",
                    expected_sha256="expected",
                )
            ],
        )


@pytest.mark.asyncio
async def test_judge_revision_emits_live_compile_and_test_point_events():
    store = RecordingStore()
    workflow = ContestWorkflow(
        store,
        resources=None,
        workspace=FrozenWorkspace(),
        judge=SuccessfulJudge(),
        model=None,
        manifests=SimpleNamespace(get=lambda _: SimpleNamespace(dataset_id="noip2018")),
    )

    compile_result, check, _ = await workflow._judge_revision(
        "run_1",
        "road",
        "submission_1",
        "revision_1",
        "source-sha",
        phase="repair",
        round_number=2,
    )

    assert compile_result.verdict == Verdict.OK
    assert check is not None and check.tests[0].test_id == "road_001"
    assert [record[0] for record in store.records] == [
        "COMPILE_COMPLETED",
        "REJUDGING",
        "JUDGE_COMPLETED",
    ]
    judge_event = store.records[-1][1]
    assert judge_event["round"] == 2
    assert judge_event["revision_id"] == "revision_1"
    assert judge_event["check"]["tests"][0]["wall_ms"] == 7
