import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from hy3_contestlens.domain import CheckResult, Verdict, CompileResult
from hy3_contestlens.evaluation import failure_triage, adjudicate
from hy3_contestlens.model import Hy3Client, complete_review_schema
from hy3_contestlens.preflight import is_hard, public_samples, validate_public, OraclePlan
from hy3_contestlens.settings import Hy3Settings
from hy3_contestlens.workflow import ContestWorkflow
from test_model_review import solution, review
from hy3_contestlens.domain import ErrorType


VALID = {"reviewer": "code_critic", "assessments": [{"step_id": "S1", "verdict": "SUPPORTED", "evidence": ["active I/O calls precede reads"], "confidence": .9}],
         "error_type": "UNRESOLVED", "first_error_step_id": None, "code_location": None, "summary": "No defect."}
ORACLE_CPP = '#include <cstdio>\nint main(){freopen("p.in","r",stdin);freopen("p.out","w",stdout);return 0;}'


@pytest.mark.parametrize("defect", ["empty", "no_evidence", "blank", "unknown", "wrong_role", "negative", "missing_location"])
def test_strict_reviews_reject_incomplete_or_inconsistent_evidence(defect):
    value = copy.deepcopy(VALID)
    if defect == "empty": value["assessments"] = []
    if defect == "no_evidence": value["assessments"][0]["evidence"] = []
    if defect == "blank": value["assessments"][0]["evidence"] = [" "]
    if defect == "unknown": value["assessments"][0]["step_id"] = "S2"
    if defect == "wrong_role": value["reviewer"] = "algorithm_critic"
    if defect in {"negative", "missing_location"}: value["assessments"][0]["verdict"] = "CONTRADICTED"
    if defect == "missing_location": value.update(error_type="WRONG_ALGORITHM", first_error_step_id="S1")
    with pytest.raises(ValidationError):
        complete_review_schema(solution(), "code_critic").model_validate(value)


@pytest.mark.asyncio
async def test_invalid_review_retries_complete_evidence_at_same_reasoning_budget(tmp_path):
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        value = {**VALID, "assessments": []} if len(requests) == 1 else VALID
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]})
    client = Hy3Client(Hy3Settings(api_key="test", reasoning_effort="high", max_tokens=127000, retry_backoff_seconds=0),
                       diagnostics_dir=tmp_path, transport=httpx.MockTransport(respond))
    result = await client.code_review({}, solution(), "road")
    assert result.assessments[0].step_id == "S1" and len(requests) == 2
    assert all(r["reasoning_effort"] == "high" and r["max_tokens"] == 127000 for r in requests)
    assert "VALIDATION FEEDBACK" in requests[1]["messages"][-1]["content"]


def test_mixed_failures_prioritize_executable_correctness():
    check = CheckResult(check_id="c", problem_id="p", dataset_id="d", verdict=Verdict.TLE, total=3, passed=0, score=0,
                        tests=[{"test_id": str(i), "verdict": v, "expected_sha256": "x", "memory_limit_mb": 256,
                                "memory_limit_source": "default"} for i,v in enumerate(["TLE", "MLE", "WA"])])
    assert failure_triage(check)["priority"] == ["WA", "MLE", "TLE"]
    a = review(ErrorType.UNRESOLVED); a.reviewer = "algorithm_critic"
    diagnosis = adjudicate(a, review(ErrorType.UNRESOLVED), Verdict.OK, check, ["S1"])
    assert diagnosis.repair_suggestion.startswith("Correctness first")
    assert "MLE" in diagnosis.repair_suggestion and "TLE" in diagnosis.repair_suggestion


def test_failed_history_is_bounded_and_excludes_private_test_payloads():
    rounds = [{"new_revision_id": "r1", "parent_revision_id": "r0", "improved": False, "attempted_change": "bad cut equality",
               "failure_triage": {"counts": {"WA": 2}}, "answer_check_result": {"private_input": "DO_NOT_SEND"},
               "public_feedback": {"counterexamples": ["two vertices"]}}]
    memory = ContestWorkflow._failure_memory(rounds)
    assert memory[0]["attempted_change"] == "bad cut equality"
    assert "DO_NOT_SEND" not in json.dumps(memory)


@pytest.mark.parametrize("probability,selected", [(0, False), (1, True)])
def test_rethink_probability_is_persisted_reproducible_and_bounded(settings, probability, selected):
    settings.rethink_probability = probability
    workflow = ContestWorkflow(None, None, SimpleNamespace(settings=settings), None, None, None)
    events = []
    workflow.store = SimpleNamespace(append_event=lambda *args: events.append(args))
    workflow._save_checkpoint = lambda *args: None
    checkpoint = {}
    assert workflow._schedule_rethink("run_test", checkpoint, 2, 4) == selected
    assert workflow._schedule_rethink("run_test", checkpoint, 2, 4) == selected
    assert len(events) == 1
    assert not workflow._schedule_rethink("run_test", checkpoint, 4, 4)
    if selected: assert not workflow._schedule_rethink("run_test", checkpoint, 3, 4)


def test_difficulty_and_public_sample_extraction_are_explicit():
    assert is_hard("省选/NOI−") and is_hard("省选/NOI-") and is_hard("NOI/NOI+/CTS")
    assert not is_hard("提高") and not is_hard("提高+/省选−")
    assert not is_hard(None) and not is_hard("")
    assert not is_hard("入门") and not is_hard("普及+/提高−")
    spec = {"source_document": {"content": '1: ```input\n2: 2\n3: ```\n4: ```output\n5: 4\n6: ```'}}
    assert public_samples(spec) == [{"input_text": "2\n", "expected": "4\n"}]


@pytest.mark.asyncio
async def test_public_failure_short_circuits_oracle_and_resume_reuses_completed_cases():
    calls = []
    class Judge:
        def compile_cpp(self, *args):
            calls.append("compile")
            return CompileResult(verdict=Verdict.OK, compile_artifact_id="compile_1", source_artifact_id="s")
        def run_public_case(self, *args):
            calls.append("case")
            return {"verdict": "WA", "actual": "0", "wall_ms": 1}
    workflow = SimpleNamespace(judge=Judge(), _cancelled=lambda _: False,
        workspace=SimpleNamespace(freeze_cpp_revision=lambda *a: {"source_artifact_id":"s", "source_sha256":"h"}))
    sub = {"submission_id":"s", "revision_id":"r000", "sha256":"h"}
    spec = {"source_document": {"content":"```input\n2\n```\n```output\n4\n```"}}
    state, shared = {}, {}
    for _ in range(2):
        result = await validate_public(workflow, "run_x", "p", sub, spec, state, shared, lambda: None)
        assert result["defer_reviews"] and result["status"] == "SAMPLE_FAILED"
    assert calls == ["compile", "case"]


@pytest.mark.asyncio
async def test_oracle_must_pass_public_samples_before_generating_expected_answers():
    observations = []
    class Judge:
        def compile_cpp(self, p, source, sha):
            return CompileResult(verdict=Verdict.OK, compile_artifact_id=source, source_artifact_id=source)
        def run_public_case(self, binary, *args):
            observations.append(binary)
            return {"verdict": "AC" if binary == "candidate" else "WA", "actual": "4", "wall_ms": 1}
    async def oracle(*args, **kwargs):
        return OraclePlan(enumeration="Enumerate all complete possibilities.", oracle_cpp=ORACLE_CPP, cases=[{"input_text":"1", "purpose":"boundary"}])
    workspace = SimpleNamespace(freeze_cpp_revision=lambda r,s,*a: {"source_artifact_id":s, "source_sha256":"h"},
                                get_or_create_cpp_submission=lambda *a,**k: {"submission_id":"oracle", "revision_id":"r000", "sha256":"h2"})
    workflow = SimpleNamespace(judge=Judge(), workspace=workspace, model=SimpleNamespace(public_oracle=oracle),
                                manifests=SimpleNamespace(get=lambda p: SimpleNamespace(io=SimpleNamespace(basename="p"))), _cancelled=lambda _: False)
    result = await validate_public(workflow,"run_x","p",{"submission_id":"candidate","revision_id":"r000","sha256":"h"},
        {"source_document":{"content":"```input\n2\n```\n```output\n4\n```"}}, {}, {}, lambda: None)
    assert result["oracle_status"] == "unavailable" and result["reason"]["kind"] == "sample_validation_failed"
    assert observations == ["candidate", "oracle", "oracle", "oracle"]


@pytest.mark.asyncio
@pytest.mark.parametrize("problem_id", ["road", "game"])
async def test_global_memory_and_rethink_across_complete_repair_loop(settings, problem_id):
    from hy3_contestlens.store import Store
    from hy3_contestlens.workspace import WorkspaceStore
    from hy3_contestlens.datasets import ManifestCatalog
    settings.rethink_probability = 1
    store = Store(settings.database_path)
    rid = store.create_run(problem_id, {"repair": {"enabled": True, "max_rounds": 3}})["run_id"]
    store.get_binding = lambda _: {"binding_id": "b", "scope_id": "s", "document": {}}
    calls = []
    def executable_solution():
        result = solution()
        result.cpp_source = '#include <cstdio>\nint main(){freopen("BASENAME.in","r",stdin);freopen("BASENAME.out","w",stdout);return 0;}\n'.replace("BASENAME", problem_id)
        return result
    class Model:
        async def solve(self, *args): return executable_solution()
        async def repair(self, *args, **kwargs):
            calls.append(kwargs)
            revised = executable_solution()
            revised.cpp_source += f"\n// failed change {len(calls)}\n"
            return revised
        async def algorithm_review(self, *args):
            result = review(ErrorType.WRONG_ALGORITHM); result.reviewer = "algorithm_critic"
            return result
        async def code_review(self, *args, **kwargs): return review(ErrorType.WRONG_ALGORITHM)
    class Judge:
        def compile_cpp(self, p, artifact, sha):
            return CompileResult(verdict=Verdict.OK, compile_artifact_id="compile_1", source_artifact_id=artifact)
        def check_answer(self, *args):
            return CheckResult(check_id="c", problem_id=problem_id, dataset_id="noip2018", verdict=Verdict.WA, passed=0,total=1,score=0)
    workflow = ContestWorkflow(store, None, WorkspaceStore(settings), Judge(), Model(), ManifestCatalog(settings.manifests_root))
    # No public samples: global repair changes must still apply.
    workflow._write(rid,"workflow_checkpoint.json", {"schema_version":1,"run_id":rid,"problem_spec":{"summary":"fixture"},
                                                     "document":{},"public_problem":{},"binding_id":"b"})
    await workflow._execute(rid)
    assert len(calls) == 3 and [c["rethink"] for c in calls] == [False, False, True]
    assert calls[0]["failure_memory"] == []
    assert len(calls[1]["failure_memory"]) == 1 and len(calls[2]["failure_memory"]) == 2
    assert "failed change 1" in calls[1]["failure_memory"][0]["attempted_change"]
    assert store.get_run(rid)["result"]["repair_round_count"] == 3


@pytest.mark.asyncio
async def test_small_differential_mismatch_is_preserved_as_tentative_evidence():
    class Judge:
        def compile_cpp(self, p, source, sha):
            return CompileResult(verdict=Verdict.OK, compile_artifact_id=source, source_artifact_id=source)
        def run_public_case(self, binary, p, inp, expected):
            actual = str(int(inp) * 2 if binary == "oracle" or int(inp) == 2 else 0) + "\n"
            return {"verdict":"AC" if actual == expected else "WA", "actual":actual,"wall_ms":1}
    async def oracle(*args):
        return OraclePlan(enumeration="Enumerate all possible choices for small n.", oracle_cpp=ORACLE_CPP,
                          cases=[{"input_text":"1\n", "purpose":"boundary"}])
    workspace = SimpleNamespace(freeze_cpp_revision=lambda r,s,*a: {"source_artifact_id":s,"source_sha256":"h"},
        get_or_create_cpp_submission=lambda *a,**k: {"submission_id":"oracle","revision_id":"r000","sha256":"o"})
    workflow = SimpleNamespace(judge=Judge(),workspace=workspace,model=SimpleNamespace(public_oracle=oracle),
        manifests=SimpleNamespace(get=lambda p: SimpleNamespace(io=SimpleNamespace(basename="p"))),_cancelled=lambda _:False)
    result = await validate_public(workflow,"run_x","p",{"submission_id":"candidate","revision_id":"r000","sha256":"h"},
        {"source_document":{"content":"```input\n2\n```\n```output\n4\n```"}}, {}, {}, lambda:None)
    assert result["status"] == "DIFFERENTIAL_MISMATCH"
    assert result["small_cases"][0]["expected"] == "2\n" and result["small_cases"][0]["actual"] == "0\n"
    assert "not formally proven" in result["oracle_caveat"]


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
async def test_proof_only_repair_is_reviewed_without_recompiling(settings, resume):
    from hy3_contestlens.store import Store
    from hy3_contestlens.workspace import WorkspaceStore
    from hy3_contestlens.datasets import ManifestCatalog
    store = Store(settings.database_path)
    rid = store.create_run("road", {"repair":{"enabled":True,"max_rounds":3}})["run_id"]
    store.get_binding = lambda _: {"binding_id":"b","scope_id":"s","document":{}}
    counts = {"compile":0,"judge":0,"repair":0,"reviews":0}
    original = solution()
    original.cpp_source = '#include <cstdio>\nint main(){freopen("road.in","r",stdin);freopen("road.out","w",stdout);return 0;}'
    class Model:
        interrupted = False
        async def solve(self,*a): return original
        async def repair(self,*a,**k):
            counts["repair"] += 1
            revised = original.model_copy(deep=True)
            revised.steps[0].justification = "Corrected proof"
            return revised
        async def algorithm_review(self,spec,sol):
            counts["reviews"] += 1
            corrected = sol.steps[0].justification == "Corrected proof"
            if resume and corrected and not self.interrupted:
                self.interrupted = True
                raise asyncio.CancelledError()
            r = review(ErrorType.UNRESOLVED if corrected else ErrorType.PROOF_GAP)
            r.reviewer = "algorithm_critic"
            return r
        async def code_review(self,*a,**k): return review(ErrorType.UNRESOLVED)
    class Judge:
        def compile_cpp(self,p,artifact,sha):
            counts["compile"] += 1
            return CompileResult(verdict=Verdict.OK,source_artifact_id=artifact,compile_artifact_id="compile_x")
        def check_answer(self,*a):
            counts["judge"] += 1
            return CheckResult(check_id="c",problem_id="road",dataset_id="noip2018",verdict=Verdict.AC,passed=1,total=1,score=100)
    w=ContestWorkflow(store,None,WorkspaceStore(settings),Judge(),Model(),ManifestCatalog(settings.manifests_root))
    w._write(rid,"workflow_checkpoint.json",{"schema_version":1,"run_id":rid,"problem_spec":{},"document":{},"public_problem":{},"binding_id":"b"})
    if resume:
        with pytest.raises(asyncio.CancelledError): await w._execute(rid)
    await w._execute(rid)
    result=store.get_run(rid)["result"]
    assert result["stop_reason"] == "COMPLETED"
    assert counts["compile"] == counts["judge"] == counts["repair"] == 1
    assert result["proof_only_round_count"] == 1 and result["code_revision_count"] == 0
    assert result["best_submission_result"]["revision_id"] == "r000"
    r=result["repair_round_results"][0]
    assert r["judge_reused"] and r["reasoning_revision_id"] == "proof_001"
    saved=json.loads((settings.runs_root/rid/r["solution_artifact"]).read_text(encoding="utf-8"))
    assert saved["steps"][0]["justification"] == "Corrected proof"
    from hy3_contestlens.report_translation import report_sections
    sections = report_sections(result, settings.runs_root/rid)
    assert sections[0]["critics"][0]["review"]["error_type"] == "UNRESOLVED"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["incomplete", "compile", "sample"])
async def test_oracle_corrects_failed_program_and_reuses_successful_checkpoint(failure):
    calls=[]
    class Judge:
        def compile_cpp(self,p,source,sha):
            return CompileResult(verdict=Verdict.CE if source=="bad" and failure=="compile" else Verdict.OK,
                                 source_artifact_id=source,compile_artifact_id=source,diagnostics=["syntax error"])
        def run_public_case(self,binary,p,inp,expected):
            actual="0\n" if binary=="bad" else str(int(inp)*2)+"\n"
            return {"verdict":"AC" if actual==expected else "WA","actual":actual,"wall_ms":1}
    async def oracle(*a,**kw):
        calls.append(kw)
        code = '#include <cstdio>int main(){freopen(' if len(calls)==1 and failure=="incomplete" else ORACLE_CPP
        if len(calls)==1: code += '// bad'
        return OraclePlan(enumeration="Enumerate all possible complete outcomes.",oracle_cpp=code,cases=[{"input_text":"1\n","purpose":"boundary"}])
    workspace=SimpleNamespace(freeze_cpp_revision=lambda r,s,*a:{"source_artifact_id":s,"source_sha256":"h"},
        get_or_create_cpp_submission=lambda r,p,source,**kw:{"submission_id":"bad" if '// bad' in source else "good","revision_id":"r000","sha256":"h"})
    w=SimpleNamespace(judge=Judge(),workspace=workspace,model=SimpleNamespace(public_oracle=oracle),_cancelled=lambda _:False,
                      manifests=SimpleNamespace(get=lambda _:SimpleNamespace(io=SimpleNamespace(basename="p"))))
    state,shared={},{}
    args=(w,"run_x","p",{"submission_id":"candidate","revision_id":"r000","sha256":"h"},
          {"source_document":{"content":"```input\n2\n```\n```output\n4\n```"}},state,shared,lambda:None)
    for _ in range(2):
        result=await validate_public(*args)
        assert result["status"]=="PASSED"
    assert len(calls)==2 and "correction" in calls[1]
    assert shared["validated_attempt"]==1 and shared["attempts"][0]["failure"]
