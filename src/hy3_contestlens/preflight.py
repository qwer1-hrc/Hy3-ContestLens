"""Bounded public evidence before expensive reviews. No private dataset access."""
from __future__ import annotations

import asyncio
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .errors import ContestLensError


class SmallCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_text: str = Field(min_length=1, max_length=4096)
    purpose: str = Field(min_length=1, max_length=300)


class OraclePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enumeration: str = Field(min_length=20, max_length=3000)
    oracle_cpp: str = Field(min_length=20, max_length=64000)
    cases: list[SmallCase] = Field(min_length=1, max_length=6)


def is_hard(difficulty: str | None) -> bool:
    return bool(difficulty and difficulty.strip().startswith(("省选/NOI", "NOI/")))


def public_samples(spec: dict[str, Any]) -> list[dict[str, str]]:
    text = spec.get("source_document", {}).get("content", "").split("[UNTRUSTED IMAGE DESCRIPTIONS")[0]
    text = re.sub(r"^\d+: ?", "", text, flags=re.M)
    samples, pending = [], None
    previous_end = 0
    for match in re.finditer(r"```([^\n]*)\n(.*?)```", text, re.S):
        tag, content = match.group(1).strip().lower(), match.group(2).strip() + "\n"
        heading = text[previous_end:match.start()].strip().splitlines()
        heading = heading[-1].lower() if heading else ""
        previous_end = match.end()
        if len(content.encode()) > 16384:
            pending = None
            continue
        kind = tag if tag in {"input", "output"} else (
            "input" if re.search(r"样例.*输入|输入.*样例|sample input|input example", heading) else
            "output" if re.search(r"样例.*输出|输出.*样例|sample output|output example", heading) else None)
        if kind == "input":
            pending = content
        elif kind == "output" and pending is not None:
            samples.append({"input_text": pending, "expected": content})
            pending = None
    return samples[:4]


async def validate_public(workflow, run_id, problem_id, submission, spec, state, shared, save):
    """Persist every completed external operation so recovery can reuse it."""
    samples = public_samples(spec)
    if not samples:
        return {"status": "UNAVAILABLE", "reason": "No unambiguous public sample pairs could be extracted"}

    async def compile_submission(sub, target):
        if "compile" not in target:
            frozen = workflow.workspace.freeze_cpp_revision(run_id, sub["submission_id"], sub["revision_id"], sub["sha256"])
            compiled = await asyncio.to_thread(workflow.judge.compile_cpp, problem_id, frozen["source_artifact_id"], frozen["source_sha256"])
            target.update(frozen=frozen, compile=compiled.model_dump(mode="json"))
            save()
        return target["compile"]

    async def run(binary, case, rows, index):
        if len(rows) <= index:
            if workflow._cancelled(run_id):
                raise asyncio.CancelledError()
            observation = await asyncio.to_thread(workflow.judge.run_public_case, binary, problem_id,
                                                  case["input_text"], case.get("expected", ""))
            rows.append({**case, **observation})
            save()
        return rows[index]

    compiled = await compile_submission(submission, state)
    if compiled["verdict"] != "OK":
        return {"status": "COMPILE_FAILED", "defer_reviews": True, "samples": [], "small_cases": []}
    binary = compiled["compile_artifact_id"]
    rows = state.setdefault("samples", [])
    for i, case in enumerate(samples):
        result = await run(binary, case, rows, i)
        if result["verdict"] != "AC":
            return {"status": "SAMPLE_FAILED", "defer_reviews": True, "samples": rows,
                    "small_cases": [], "oracle_status": "deferred_until_samples_pass"}

    attempts = shared.setdefault("attempts", [])
    if not attempts and "plan" in shared:
        attempts.append({k: shared[k] for k in ("plan", "compile", "frozen", "samples", "small_cases") if k in shared})
        save()
    if shared.get("exhausted"):
        return {"status": "SAMPLES_PASSED", "samples": rows, "oracle_status": "unavailable", "oracle_attempts": len(attempts)}
    active = None
    for index in range(3):
        if workflow._cancelled(run_id):
            raise asyncio.CancelledError()
        if len(attempts) <= index:
            attempts.append({})
            save()
        active = attempts[index]
        if active.get("failure"):
            continue
        if "plan" not in active:
            correction = None
            if index:
                previous = attempts[index - 1]
                correction = {"failure": previous.get("failure"), "previous_source": previous.get("plan", {}).get("oracle_cpp", "")}
            try:
                basename = workflow.manifests.get(problem_id).io.basename
                generated = (await workflow.model.public_oracle(spec, basename, correction=correction) if correction
                             else await workflow.model.public_oracle(spec, basename))
                active["plan"] = generated.model_dump(mode="json")
                save()
            except ContestLensError as exc:
                active["failure"] = {"kind": "model_call_failed", "code": exc.code}
                save()
                break  # Transport/JSON retries are already owned by the model client.
        try:
            plan = OraclePlan.model_validate(active["plan"])
            from .workspace import validate_cpp
            validate_cpp(plan.oracle_cpp, 64000, workflow.manifests.get(problem_id).io.basename)
            oracle_sub = workflow.workspace.get_or_create_cpp_submission(run_id, problem_id, plan.oracle_cpp, created_by="public_oracle")
            oracle_compile = await compile_submission(oracle_sub, active)
            if oracle_compile["verdict"] != "OK":
                active["failure"] = {"kind": "compile_failed", "verdict": oracle_compile["verdict"],
                                     "diagnostics": oracle_compile.get("diagnostics", [])[:20]}
                save()
                if oracle_compile["verdict"] == "SANDBOX_UNAVAILABLE":
                    break
                continue
            oracle_binary = oracle_compile["compile_artifact_id"]
            oracle_samples = active.setdefault("samples", [])
            for i, case in enumerate(samples):
                observation = await run(oracle_binary, case, oracle_samples, i)
                if observation["verdict"] != "AC":
                    active["failure"] = {"kind": "sample_validation_failed", "counterexample": observation}
                    break
            if "failure" not in active:
                oracle_cases = active.setdefault("small_cases", [])
                for i, case in enumerate(plan.cases):
                    observation = await run(oracle_binary, case.model_dump(), oracle_cases, i)
                    if observation["verdict"] not in {"AC", "WA"} or not observation["actual"].strip():
                        active["failure"] = {"kind": "execution_failed", "counterexample": observation}
                        break
            save()
            if "failure" not in active:
                shared["validated_attempt"] = index
                shared.pop("error", None)
                save()
                break
        except (ContestLensError, ValueError) as exc:
            active["failure"] = {"kind": "invalid_program", "code": getattr(exc, "code", "INVALID_ORACLE_PLAN"),
                                 "instruction": "Supply complete compilable C++ with both mandatory freopen calls and the full enumeration."}
            save()
    if active is None or "failure" in active:
        shared["exhausted"] = True
        save()
        return {"status": "SAMPLES_PASSED", "samples": rows, "oracle_status": "unavailable",
                "oracle_attempts": len(attempts), "reason": (active or {}).get("failure", {})}
    # Caches are namespaced by oracle candidate so a corrected oracle cannot
    # accidentally reuse expected outputs from the rejected candidate.
    small = state.setdefault("oracle_candidate_results", {}).setdefault(str(shared["validated_attempt"]), [])
    for i, case in enumerate(plan.cases):
        await run(binary, {**case.model_dump(), "expected": active["small_cases"][i]["actual"]}, small, i)
    return {"status": "DIFFERENTIAL_MISMATCH" if any(c["verdict"] != "AC" for c in small) else "PASSED",
            "samples": rows, "small_cases": small, "oracle_status": "sample_cross_checked", "oracle_attempts": len(attempts),
            "oracle_caveat": "A generated exhaustive oracle is cross-checked, not formally proven. Disagreements require investigation."}
