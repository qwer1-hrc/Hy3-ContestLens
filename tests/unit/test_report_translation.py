import asyncio
import json
from types import SimpleNamespace

import pytest

from hy3_contestlens.domain import Verdict
from hy3_contestlens.errors import ContestLensError
from hy3_contestlens.model_diagnostics import current_model_run
from hy3_contestlens.report_translation import (
    JUDGE_VERDICT_LABELS, ReportTranslationService, localize, machine_marker_text,
    narrative_texts, report_sections, text_key,
)
from hy3_contestlens.settings import ReportTranslationSettings
from hy3_contestlens.store import Store
from hy3_contestlens.utils import atomic_write_json


class Translator:
    settings = SimpleNamespace(model="mock-translator")

    def __init__(self):
        self.calls = []
        self.fail_at = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def translate_report_texts(self, texts):
        self.calls.append(dict(texts))
        self.started.set()
        await self.release.wait()
        if self.fail_at == len(self.calls):
            raise ContestLensError("HY3_INVALID_RESPONSE", "secret provider diagnostic", status_code=502)
        return {key: "中文证据与评审说明。" for key in texts}


@pytest.fixture
def history(settings, monkeypatch):
    store = Store(settings.database_path)

    def add(run_id, *, state="COMPLETED", evidence=None, complete=True):
        with monkeypatch.context() as patch:
            patch.setattr("hy3_contestlens.store.safe_id", lambda prefix: run_id)
            store.create_run("road", {"problem_id": "road"})
        evaluation = {
            "revision_id": "v1", "check": None,
            "diagnosis": {
                "process_correct": True, "error_type": "UNRESOLVED",
                "evidence": evidence if evidence is not None else [f"{run_id} evidence."],
            },
        }
        result = {
            "run_id": run_id, "problem_id": "road", "stop_reason": "COMPLETED",
            "initial_submission_result": evaluation, "best_submission_result": evaluation,
        } if complete else {"error_code": "HY3_NOT_CONFIGURED"}
        store.update_run(run_id, state, result=result)
        atomic_write_json(settings.runs_root / run_id / "final_evaluation.json", result)
        return result

    return store, add


async def finish(service):
    if service._worker:
        await asyncio.wait_for(service._worker, timeout=3)


@pytest.mark.asyncio
async def test_reads_never_start_model_and_batch_obeys_directory_name_order(settings, history):
    store, add = history
    for run_id in ("run_c", "run_a", "run_b"):
        add(run_id)
    add("run_empty", state="FAILED", complete=False)
    add("run_active", state="SOLVING")
    model = Translator()
    service = ReportTranslationService(settings.runs_root, store, model)
    assert service.document("run_a")["state"]["status"] == "PENDING"
    assert service.snapshot()["busy"] is False
    assert service._worker is None and model.calls == []

    service.enqueue()
    await finish(service)
    assert [next(iter(call.values())) for call in model.calls] == ["run_a evidence.", "run_b evidence.", "run_c evidence."]
    assert service.status("run_empty")["status"] == "EMPTY"
    assert service.status("run_active")["status"] == "EMPTY"


@pytest.mark.asyncio
async def test_priority_is_first_and_concurrent_visits_are_deduplicated(settings, history):
    store, add = history
    for run_id in ("run_a", "run_b", "run_c"):
        add(run_id)
    model = Translator()
    service = ReportTranslationService(settings.runs_root, store, model)
    service.enqueue(priority_run_id="run_c")
    service.enqueue()
    service.enqueue(priority_run_id="run_c")
    await finish(service)
    assert [next(iter(call.values())) for call in model.calls] == ["run_c evidence.", "run_a evidence.", "run_b evidence."]


@pytest.mark.asyncio
async def test_priority_promotes_next_report_without_restarting_inflight_call(settings, history):
    store, add = history
    for run_id in ("run_a", "run_b", "run_c"):
        add(run_id)
    model = Translator()
    model.release.clear()
    service = ReportTranslationService(settings.runs_root, store, model)
    service.enqueue()
    await asyncio.wait_for(model.started.wait(), timeout=2)
    service.enqueue(priority_run_id="run_c")
    assert service.status("run_c")["position"] == 1
    assert len(model.calls) == 1
    model.release.set()
    await finish(service)
    assert [next(iter(call.values())) for call in model.calls] == ["run_a evidence.", "run_c evidence.", "run_b evidence."]


@pytest.mark.asyncio
async def test_ready_cache_survives_restart_and_raw_workflow_artifacts_are_immutable(settings, history):
    store, add = history
    add("run_a")
    path = settings.runs_root / "run_a" / "final_evaluation.json"
    original = path.read_bytes()
    events = store.list_events("run_a")
    model = Translator()
    first = ReportTranslationService(settings.runs_root, store, model)
    first.enqueue()
    await finish(first)
    second = ReportTranslationService(settings.runs_root, store, model, ReportTranslationSettings(max_tokens=2048))
    assert second.status("run_a")["processed"] is True
    second.enqueue(priority_run_id="run_a")
    assert second._worker is None
    assert len(model.calls) == 1
    assert path.read_bytes() == original
    assert store.list_events("run_a") == events
    assert store.get_run("run_a")["status"] == "COMPLETED"

    result = store.get_run("run_a")["result"]
    result["best_submission_result"]["diagnosis"]["evidence"] = ["New evidence."]
    store.update_run("run_a", "COMPLETED", result=result)
    assert second.status("run_a")["status"] == "PENDING"
    second.enqueue()
    await finish(second)
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_failure_is_marked_and_only_explicit_retry_calls_model_again(settings, history):
    store, add = history
    add("run_a")
    add("run_b")
    model = Translator()
    model.fail_at = 1
    service = ReportTranslationService(settings.runs_root, store, model, ReportTranslationSettings(max_attempts=1))
    service.enqueue()
    await finish(service)
    assert service.status("run_a")["status"] == "FAILED"
    assert service.status("run_b")["status"] == "READY"
    restarted = ReportTranslationService(settings.runs_root, store, model)
    restarted.enqueue(priority_run_id="run_a")
    assert restarted._worker is None and len(model.calls) == 2
    assert "secret" not in json.dumps(restarted.snapshot())
    restarted.enqueue(retry_run_id="run_a")
    await finish(restarted)
    assert restarted.status("run_a")["processed"] is True
    assert len(model.calls) == 3


@pytest.mark.asyncio
async def test_partial_translation_is_checkpointed_and_resumed(settings, history):
    store, add = history
    add("run_a", evidence=[f"Evidence {index}. " + "x" * 1990 for index in range(5)])
    model = Translator()
    model.fail_at = 2
    service = ReportTranslationService(settings.runs_root, store, model, ReportTranslationSettings(max_attempts=1))
    service.enqueue()
    await finish(service)
    assert service.status("run_a")["status"] == "FAILED"
    successful_keys = set(model.calls[0])
    service.enqueue(retry_run_id="run_a")
    await finish(service)
    assert service.status("run_a")["processed"]
    assert not successful_keys.intersection(key for batch in model.calls[2:] for key in batch)


@pytest.mark.asyncio
async def test_shutdown_does_not_mark_interrupted_request_as_processed(settings, history):
    store, add = history
    add("run_a")
    model = Translator()
    model.release.clear()
    service = ReportTranslationService(settings.runs_root, store, model)
    service.enqueue()
    await asyncio.wait_for(model.started.wait(), timeout=2)
    await service.close()
    restarted = ReportTranslationService(settings.runs_root, store, model)
    assert restarted.status("run_a")["status"] == "PENDING"
    assert restarted._worker is None
    model.release.set()
    restarted.enqueue()
    await finish(restarted)
    assert restarted.status("run_a")["processed"]


@pytest.mark.asyncio
async def test_empty_prose_is_marked_ready_without_unnecessary_model_call(settings, history):
    store, add = history
    add("run_a", evidence=[])
    model = Translator()
    service = ReportTranslationService(settings.runs_root, store, model)
    service.enqueue()
    await finish(service)
    assert service.status("run_a")["processed"]
    assert model.calls == []


def test_source_includes_initial_round_and_best_critics_but_not_hidden_tests(settings, history):
    store, add = history
    result = add("run_a")
    root = settings.runs_root / "run_a"
    critic = {
        "summary": "Initial algorithm review.",
        "assessments": [{"step_id": "S1", "verdict": "SUPPORTED", "evidence": ["An invariant holds."]}],
    }
    atomic_write_json(root / "algorithm_critic.json", critic)
    result["repair_round_results"] = [{
        "new_revision_id": "v2", "process_evaluation": {"evidence": ["Repaired evidence."]},
        "code_review": {"summary": "Repaired code review."},
        "repair_plan": {"root_cause": ["Wrong boundary."], "required_changes": ["Fix boundary."]},
        "quality_gate": {"passed": True, "fixed_tests": ["game1"], "regressed_tests": []},
    }]
    result["best_submission_result"] = {**result["best_submission_result"], "revision_id": "v2"}
    result["private_expected_output"] = "SECRET ANSWER"
    sections = report_sections(result, root)
    assert sections[0]["critics"][0]["review"]["summary"] == "Repaired code review."
    assert sections[2]["quality_gate"]["fixed_tests"] == ["game1"]
    texts = list(narrative_texts(sections).values())
    assert "Initial algorithm review." in texts and "An invariant holds." in texts
    assert "Fix boundary." in texts and "Repaired evidence." in texts
    assert "SECRET ANSWER" not in texts


class PartialTranslator(Translator):
    async def translate_report_texts(self, texts):
        self.calls.append(dict(texts))
        keys = list(texts)[:1] if len(self.calls) == 1 else texts
        return {key: "有效的中文片段。" for key in keys}


@pytest.mark.asyncio
async def test_partial_response_retries_only_missing_items(settings, history):
    store, add = history
    add("run_a", evidence=["First evidence.", "Second evidence."])
    model = PartialTranslator()
    options = ReportTranslationSettings(max_attempts=3, retry_backoff_seconds=0)
    service = ReportTranslationService(settings.runs_root, store, model, options)
    service.enqueue()
    await finish(service)
    assert [list(batch.values()) for batch in model.calls] == [
        ["First evidence.", "Second evidence."], ["Second evidence."],
    ]
    assert service.status("run_a")["processed"]
    cache = json.loads((settings.runs_root / "run_a" / "report_translation" / "zh-CN.json").read_text(encoding="utf-8"))
    assert len(cache["segments"]) == 2 and cache["failed_segments"] == {}
    assert cache["translation_profile"]["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_good_items_are_checkpointed_before_exhaustion_and_survive_restart(settings, history):
    store, add = history
    add("run_a", evidence=["First evidence.", "Second evidence."])
    model = PartialTranslator()
    service = ReportTranslationService(settings.runs_root, store, model, ReportTranslationSettings(max_attempts=1))
    service.enqueue()
    await finish(service)
    assert service.status("run_a")["status"] == "FAILED"
    assert service.status("run_a")["completed_segments"] == 1
    restarted = ReportTranslationService(settings.runs_root, store, model)
    restarted.enqueue(retry_run_id="run_a")
    await finish(restarted)
    assert list(model.calls[1].values()) == ["Second evidence."]
    assert restarted.status("run_a")["processed"]


@pytest.mark.asyncio
async def test_small_batch_limits_apply_to_all_requests(settings, history):
    store, add = history
    add("run_a", evidence=[f"Evidence {index}. " + "x" * size for index, size in enumerate([500, 500, 500, 1400, 1400])])
    model = Translator()
    service = ReportTranslationService(settings.runs_root, store, model)
    service.enqueue()
    await finish(service)
    assert service.status("run_a")["processed"]
    assert len(model.calls) > 1
    assert all(len(batch) <= 2 and sum(map(len, batch.values())) <= 2000 for batch in model.calls)


@pytest.mark.asyncio
async def test_transient_request_retries_are_bounded_without_losing_progress(settings, history):
    store, add = history
    add("run_a")
    model = Translator()
    model.fail_at = 1
    service = ReportTranslationService(settings.runs_root, store, model, ReportTranslationSettings(retry_backoff_seconds=0))
    service.enqueue()
    await finish(service)
    assert service.status("run_a")["processed"]
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_auth_failure_is_not_retried_three_times(settings, history):
    store, add = history
    add("run_a")

    class DeniedTranslator(Translator):
        async def translate_report_texts(self, texts):
            self.calls.append(dict(texts))
            raise ContestLensError("HY3_INVALID_RESPONSE", "Denied", {"failure_kind": "http_error", "http_status": 401}, 502)

    model = DeniedTranslator()
    service = ReportTranslationService(settings.runs_root, store, model, ReportTranslationSettings(retry_backoff_seconds=0))
    service.enqueue()
    await finish(service)
    assert service.status("run_a")["status"] == "FAILED"
    assert len(model.calls) == 1


@pytest.mark.parametrize("verdict", list(Verdict))
def test_all_system_verdict_markers_have_local_labels(verdict):
    assert verdict.value in JUDGE_VERDICT_LABELS
    marker = f"deterministic_judge_verdict={verdict.value}"
    assert machine_marker_text(marker) == f"确定性判题结果：{JUDGE_VERDICT_LABELS[verdict.value]}（{verdict.value}）"
    assert narrative_texts({"evidence": [marker]}) == {}


def test_marker_detection_is_exact_and_does_not_suppress_real_prose():
    prose = [
        "The deterministic_judge_verdict=WA result contradicts S1.",
        "deterministic_judge_verdict=WA because the boundary is wrong.",
        "x = INF", "WA", "Unknown evidence.",
    ]
    marker = " \tdeterministic_judge_verdict = TLE\n"
    source = {"evidence": [marker, *prose]}
    assert list(narrative_texts(source).values()) == prose
    assert machine_marker_text(marker) == "确定性判题结果：超出时间限制（TLE）"
    assert machine_marker_text("deterministic_judge_verdict=FUTURE_CODE") == "确定性判题结果：未知状态（FUTURE_CODE）"


def test_local_marker_mapping_overrides_old_cached_translation():
    marker = "deterministic_judge_verdict=WA"
    source = {"evidence": [marker, "Normal evidence."]}
    localized = localize(source, {text_key(marker): "错误的旧译文", text_key("Normal evidence."): "正常证据。"})
    assert localized["evidence"] == ["确定性判题结果：答案错误（WA）", "正常证据。"]
    assert source["evidence"][0] == marker


@pytest.mark.asyncio
async def test_mixed_report_never_sends_machine_markers_to_model(settings, history):
    store, add = history
    marker = "deterministic_judge_verdict=WA"
    add("run_a", evidence=[marker, "A boundary is incorrect.", "Another real explanation."])
    model = Translator()
    service = ReportTranslationService(settings.runs_root, store, model)
    service.enqueue()
    await finish(service)
    assert service.status("run_a")["processed"]
    sent = [value for batch in model.calls for value in batch.values()]
    assert sent == ["A boundary is incorrect.", "Another real explanation."]
    assert service.document("run_a")["sections"][0]["diagnosis"]["evidence"][0] == "确定性判题结果：答案错误（WA）"


@pytest.mark.asyncio
async def test_machine_only_report_finishes_without_model_calls(settings, history):
    store, add = history
    add("run_a", evidence=["deterministic_judge_verdict=WA", "deterministic_judge_verdict=CE"])
    model = Translator()
    service = ReportTranslationService(settings.runs_root, store, model)
    service.enqueue()
    await finish(service)
    assert service.status("run_a")["processed"]
    assert service.status("run_a")["total_segments"] == 0
    assert model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("has_more_prose", [False, True])
async def test_legacy_failed_marker_cache_reuses_successful_segments(settings, history, has_more_prose):
    store, add = history
    marker = "deterministic_judge_verdict=WA"
    evidence = [marker, "Already translated evidence."]
    if has_more_prose:
        evidence.append("Still untranslated evidence.")
    add("run_a", evidence=evidence)
    result_path = settings.runs_root / "run_a" / "final_evaluation.json"
    original = result_path.read_bytes()
    model = Translator()
    service = ReportTranslationService(settings.runs_root, store, model)
    source_hash = service._source("run_a")[1]
    cache_path = settings.runs_root / "run_a" / "report_translation" / "zh-CN.json"
    atomic_write_json(cache_path, {
        "schema_version": 1, "source_sha256": source_hash, "status": "FAILED",
        "error_code": "REPORT_TRANSLATION_INCOMPLETE", "total_segments": len(evidence),
        "segments": {f"{text_key(evidence[1])}:0": "已保存的中文证据。"},
        "failed_segments": {f"{text_key(marker)}:0": {"attempts": 3, "error_code": "REPORT_TRANSLATION_INCOMPLETE"}},
    })
    assert service.status("run_a")["status"] == "FAILED"
    service.enqueue(retry_run_id="run_a")
    await finish(service)
    assert service.status("run_a")["processed"]
    sent = [value for batch in model.calls for value in batch.values()]
    assert sent == ([evidence[2]] if has_more_prose else [])
    assert service.document("run_a")["sections"][0]["diagnosis"]["evidence"][:2] == [
        "确定性判题结果：答案错误（WA）", "已保存的中文证据。",
    ]
    assert result_path.read_bytes() == original
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache["source_sha256"] == source_hash and cache["failed_segments"] == {}


@pytest.mark.asyncio
async def test_ready_cache_does_not_require_a_model_translation_for_machine_markers(settings, history):
    store, add = history
    marker = "deterministic_judge_verdict=WA"
    prose = "Already translated evidence."
    add("run_a", evidence=[marker, prose])
    model = Translator()
    service = ReportTranslationService(settings.runs_root, store, model)
    cache_path = settings.runs_root / "run_a" / "report_translation" / "zh-CN.json"
    atomic_write_json(cache_path, {
        "schema_version": 1, "source_sha256": service._source("run_a")[1], "status": "READY",
        "translations": {text_key(prose): "已经保存的译文。"},
    })
    original_cache = cache_path.read_bytes()
    assert service.status("run_a")["processed"]
    service.enqueue(priority_run_id="run_a")
    assert service._worker is None and model.calls == []
    assert service.document("run_a")["sections"][0]["diagnosis"]["evidence"] == [
        "确定性判题结果：答案错误（WA）", "已经保存的译文。",
    ]
    assert cache_path.read_bytes() == original_cache


class ControlledTranslator(Translator):
    """Hold individual responses to test real overlap without wall-clock benchmarks."""
    def __init__(self):
        super().__init__()
        self.incoming = asyncio.Queue()
        self.gates = []
        self.active = 0
        self.peak = 0
        self.cancelled = []
        self.contexts = []

    async def translate_report_texts(self, texts):
        index = len(self.calls)
        self.calls.append(dict(texts))
        self.contexts.append(current_model_run.get().run_id)
        gate = asyncio.get_running_loop().create_future()
        self.gates.append(gate)
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.incoming.put_nowait(index)
        try:
            result = await gate
            if isinstance(result, Exception):
                raise result
            return result if result is not None else {key: f"中文批次{index}。" for key in texts}
        except asyncio.CancelledError:
            self.cancelled.append(index)
            raise
        finally:
            self.active -= 1

    async def next_batch(self):
        return await asyncio.wait_for(self.incoming.get(), timeout=3)

    def respond(self, index, result=None):
        self.gates[index].set_result(result)


@pytest.mark.asyncio
async def test_two_batches_overlap_and_out_of_order_results_are_not_lost(settings, history):
    store, add = history
    evidence = [f"Evidence {index}." for index in range(4)]
    add("run_a", evidence=evidence)
    model = ControlledTranslator()
    service = ReportTranslationService(settings.runs_root, store, model, ReportTranslationSettings(batch_max_items=1))
    try:
        service.enqueue()
        assert [await model.next_batch(), await model.next_batch()] == [0, 1]
        assert model.active == model.peak == 2
        service.enqueue(priority_run_id="run_a")
        service.enqueue()
        assert len(model.calls) == 2
        model.respond(1)
        assert await model.next_batch() == 2
        assert service.status("run_a")["completed_segments"] == 1
        model.respond(2)
        assert await model.next_batch() == 3
        assert service.status("run_a")["completed_segments"] == 2
        model.respond(3)
        model.respond(0)
        await finish(service)
        assert service.status("run_a")["processed"]
        assert len(model.calls) == 4 and model.peak == 2
        assert service.document("run_a")["sections"][0]["diagnosis"]["evidence"] == [f"中文批次{index}。" for index in range(4)]
        cache = json.loads((settings.runs_root / "run_a" / "report_translation" / "zh-CN.json").read_text(encoding="utf-8"))
        assert len(cache["segments"]) == 4 and cache["failed_segments"] == {}
        assert cache["translation_profile"]["max_concurrency"] == 2
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_concurrency_one_restores_serial_requests(settings, history):
    store, add = history
    add("run_a", evidence=["First.", "Second."])
    model = ControlledTranslator()
    service = ReportTranslationService(settings.runs_root, store, model, ReportTranslationSettings(batch_max_items=1, max_concurrency=1))
    try:
        service.enqueue()
        assert await model.next_batch() == 0
        await asyncio.sleep(0)
        assert len(model.calls) == 1 and model.incoming.empty()
        model.respond(0)
        assert await model.next_batch() == 1
        model.respond(1)
        await finish(service)
        assert service.status("run_a")["processed"] and model.peak == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_failed_batch_keeps_inflight_success_and_stops_new_batches(settings, history):
    store, add = history
    evidence = ["First.", "Second.", "Third."]
    add("run_a", evidence=evidence)
    model = ControlledTranslator()
    service = ReportTranslationService(settings.runs_root, store, model, ReportTranslationSettings(batch_max_items=1, max_attempts=1))
    try:
        service.enqueue()
        assert [await model.next_batch(), await model.next_batch()] == [0, 1]
        model.respond(0, ContestLensError("HY3_INVALID_RESPONSE", "Failed"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not service._worker.done() and len(model.calls) == 2
        model.respond(1)
        await finish(service)
        assert service.status("run_a")["status"] == "FAILED"
        assert service.status("run_a")["completed_segments"] == 1
        assert len(model.calls) == 2
        service.enqueue(retry_run_id="run_a")
        assert [await model.next_batch(), await model.next_batch()] == [2, 3]
        assert [next(iter(batch.values())) for batch in model.calls[2:]] == ["First.", "Third."]
        model.respond(2)
        model.respond(3)
        await finish(service)
        assert service.status("run_a")["processed"] and model.peak == 2
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_shutdown_joins_all_batches_and_preserves_completed_checkpoint(settings, history):
    store, add = history
    evidence = [f"Evidence {index}." for index in range(4)]
    add("run_a", evidence=evidence)
    model = ControlledTranslator()
    options = ReportTranslationSettings(batch_max_items=1)
    service = ReportTranslationService(settings.runs_root, store, model, options)
    try:
        service.enqueue()
        assert [await model.next_batch(), await model.next_batch()] == [0, 1]
        model.respond(0)
        assert await model.next_batch() == 2
        await service.close()
        assert model.active == 0 and sorted(model.cancelled) == [1, 2]
        assert len(model.calls) == 3
        assert service.status("run_a")["status"] == "PENDING"
        assert service.status("run_a")["completed_segments"] == 1
    finally:
        await service.close()
    resumed_model = Translator()
    resumed = ReportTranslationService(settings.runs_root, store, resumed_model, options)
    resumed.enqueue()
    await finish(resumed)
    assert resumed.status("run_a")["processed"]
    assert evidence[0] not in [value for batch in resumed_model.calls for value in batch.values()]


@pytest.mark.asyncio
async def test_report_priority_and_global_limit_survive_concurrent_batches(settings, history):
    store, add = history
    for run_id in ("run_a", "run_b", "run_c"):
        add(run_id, evidence=[f"{run_id} first.", f"{run_id} second."])
    model = ControlledTranslator()
    service = ReportTranslationService(settings.runs_root, store, model, ReportTranslationSettings(batch_max_items=1))
    try:
        service.enqueue()
        assert [await model.next_batch(), await model.next_batch()] == [0, 1]
        service.enqueue(priority_run_id="run_c")
        service.enqueue(priority_run_id="run_c")
        model.respond(0)
        model.respond(1)
        assert [await model.next_batch(), await model.next_batch()] == [2, 3]
        model.respond(2)
        model.respond(3)
        assert [await model.next_batch(), await model.next_batch()] == [4, 5]
        model.respond(4)
        model.respond(5)
        await finish(service)
        assert model.contexts == ["run_a", "run_a", "run_c", "run_c", "run_b", "run_b"]
        assert model.peak == 2 and len(model.calls) == 6
        assert all(service.status(run_id)["processed"] for run_id in ("run_a", "run_b", "run_c"))
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_partial_retry_does_not_resend_successful_concurrent_fragments(settings, history):
    store, add = history
    add("run_a", evidence=["First.", "Second.", "Third.", "Fourth."])
    model = ControlledTranslator()
    service = ReportTranslationService(settings.runs_root, store, model, ReportTranslationSettings(retry_backoff_seconds=0))
    try:
        service.enqueue()
        assert [await model.next_batch(), await model.next_batch()] == [0, 1]
        first_key = next(iter(model.calls[0]))
        model.respond(0, {first_key: "先保存的译文。"})
        assert await model.next_batch() == 2
        assert list(model.calls[2].values()) == ["Second."]
        assert service.status("run_a")["completed_segments"] == 1
        model.respond(1)
        model.respond(2)
        await finish(service)
        assert service.status("run_a")["completed_segments"] == 4
        assert service.status("run_a")["processed"] and model.peak == 2
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_rate_limit_cooldown_is_shared_by_retries_and_new_batches(settings, history):
    store, add = history
    add("run_a", evidence=["First.", "Second.", "Third."])
    model = ControlledTranslator()
    service = ReportTranslationService(settings.runs_root, store, model, ReportTranslationSettings(batch_max_items=1, retry_backoff_seconds=0))
    unblock = asyncio.Event()
    both_waiting = asyncio.Event()
    blocked = 0
    original_wait = service._wait_for_rate_limit

    async def controlled_wait():
        nonlocal blocked
        if service._rate_limit_until > asyncio.get_running_loop().time():
            blocked += 1
            if blocked == 2:
                both_waiting.set()
            await unblock.wait()
        await original_wait()

    service._wait_for_rate_limit = controlled_wait
    try:
        service.enqueue()
        assert [await model.next_batch(), await model.next_batch()] == [0, 1]
        model.respond(0, ContestLensError("HY3_INVALID_RESPONSE", "Rate limited", {
            "failure_kind": "http_error", "http_status": 429, "retry_after_seconds": 30,
        }, 502))
        model.respond(1)
        await asyncio.wait_for(both_waiting.wait(), timeout=3)
        assert len(model.calls) == 2
        service._rate_limit_until = 0
        unblock.set()
        assert [await model.next_batch(), await model.next_batch()] == [2, 3]
        model.respond(2)
        model.respond(3)
        await finish(service)
        assert service.status("run_a")["processed"] and model.peak == 2
    finally:
        await service.close()
