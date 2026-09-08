from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .datasets import ManifestCatalog, PrivateDataset, TestCase
from .domain import CheckResult, CompileResult, TestResult, Verdict
from .errors import ContestLensError, ensure
from .settings import AppSettings
from .utils import atomic_write_json, safe_id, sha256_bytes, sha256_file
from .workspace import WorkspaceStore


VERDICT_PRIORITY = {
    Verdict.SANDBOX_VIOLATION: 100,
    Verdict.SANDBOX_UNAVAILABLE: 95,
    Verdict.INVALID_PROBLEM_MANIFEST: 90,
    Verdict.CE: 80,
    Verdict.RE: 70,
    Verdict.MLE: 65,
    Verdict.TLE: 60,
    Verdict.OLE: 55,
    Verdict.IO_CONFLICT: 50,
    Verdict.WA: 40,
    Verdict.AC: 0,
}

CPP_COMPILE_FLAGS = (
    "-std=c++17",
    "-O2",
    "-pipe",
    "-static-libstdc++",
    "-static-libgcc",
    "-lm",
)


def normalize_noip_fulltext(data: bytes) -> bytes:
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
    lines = text.split("\n")
    return "\n".join(line.rstrip(" \t\r") for line in lines).encode("utf-8")


def compare_noip_fulltext(expected: bytes, actual: bytes) -> tuple[bool, dict[str, Any] | None, str, str]:
    normalized_expected = normalize_noip_fulltext(expected)
    normalized_actual = normalize_noip_fulltext(actual)
    expected_hash = sha256_bytes(normalized_expected)
    actual_hash = sha256_bytes(normalized_actual)
    if normalized_expected == normalized_actual:
        return True, None, expected_hash, actual_hash
    length = min(len(normalized_expected), len(normalized_actual))
    offset = next((index for index in range(length) if normalized_expected[index] != normalized_actual[index]), length)
    prefix = normalized_actual[:offset]
    line = prefix.count(b"\n") + 1
    column = offset - (prefix.rfind(b"\n") + 1) + 1
    summary = {
        "byte_offset": offset, "line": line, "column": column,
        "expected_length": len(normalized_expected), "actual_length": len(normalized_actual),
        "actual_excerpt": normalized_actual[offset : offset + 120].decode("utf-8", errors="replace"),
    }
    return False, summary, expected_hash, actual_hash


def termination_signal(exit_code: int | None) -> int | None:
    """Decode the conventional shell representation of a signal exit status."""
    if exit_code is None or isinstance(exit_code, bool):
        return None
    if exit_code < 0:
        return -exit_code
    if 128 < exit_code <= 192:
        return exit_code - 128
    return None


class DockerJudge:
    def __init__(self, settings: AppSettings, catalog: ManifestCatalog, dataset: PrivateDataset, workspace: WorkspaceStore):
        self.settings = settings
        self.catalog = catalog
        self.dataset = dataset
        self.workspace = workspace

    def _docker(self, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.settings.docker_executable, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise ContestLensError("SANDBOX_UNAVAILABLE", "Docker sandbox is unavailable", {"reason": str(exc)}, 503) from exc

    def healthcheck(self) -> dict[str, Any]:
        try:
            result = self._docker(["version", "--format", "{{.Server.Version}}"], 5)
        except ContestLensError as exc:
            return {"ready": False, "error_code": exc.code, "message": exc.message}
        if result.returncode != 0 or not result.stdout.strip():
            return {"ready": False, "error_code": "SANDBOX_UNAVAILABLE", "message": "Docker daemon is not available"}
        images: dict[str, bool] = {}
        for image in (self.settings.docker_compile_image, self.settings.docker_run_image):
            inspected = self._docker(["image", "inspect", image, "--format", "{{.Id}}"], 5)
            images[image] = inspected.returncode == 0
        return {"ready": all(images.values()), "server_version": result.stdout.strip(), "images": images}

    @staticmethod
    def _mount(path: Path, container_path: str, mode: str = "ro") -> str:
        return f"{path.resolve()}:{container_path}:{mode}"

    def compile_cpp(self, problem_id: str, source_artifact_id: str, source_sha256: str, compile_profile: str = "noip2018_cpp") -> CompileResult:
        ensure(compile_profile == "noip2018_cpp", "INVALID_COMPILE_PROFILE", "Unknown compile profile")
        self.catalog.get(problem_id)
        source_path, source_meta = self.workspace.locate_source_artifact(source_artifact_id)
        ensure(source_meta["problem_id"] == problem_id, "PROBLEM_ARTIFACT_MISMATCH", "Frozen source artifact belongs to another problem")
        ensure(source_meta["source_sha256"] == source_sha256, "SOURCE_HASH_MISMATCH", "Source hash does not match frozen artifact", status_code=409)
        health = self.healthcheck()
        if not health["ready"]:
            return CompileResult(verdict=Verdict.SANDBOX_UNAVAILABLE, source_artifact_id=source_artifact_id, diagnostics=[health.get("message", "Sandbox image is not ready")])
        run_root = self.settings.runs_root / source_meta["run_id"]
        compile_id = safe_id("compile")
        artifact_root = run_root / "artifacts" / "compile" / compile_id
        artifact_root.mkdir(parents=True, exist_ok=False)
        artifact_root.chmod(0o777)
        started = time.monotonic()
        args = [
            "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "64", "--cpus", "2",
            "--memory", "1g", "--memory-swap", "1g",
            "--mount", f"type=bind,src={source_path.resolve()},dst=/source/main.cpp,readonly",
            "--mount", f"type=bind,src={artifact_root.resolve()},dst=/build",
            self.settings.docker_compile_image,
            "g++", "/source/main.cpp", *CPP_COMPILE_FLAGS, "-o", "/build/main",
        ]
        result = self._docker(args, self.settings.compile_timeout_seconds)
        duration_ms = int((time.monotonic() - started) * 1000)
        binary = artifact_root / "main"
        diagnostics = [line for line in (result.stdout + "\n" + result.stderr).splitlines() if line][:200]
        if result.returncode != 0 or not binary.is_file():
            shutil.rmtree(artifact_root, ignore_errors=True)
            return CompileResult(verdict=Verdict.CE, source_artifact_id=source_artifact_id, compiler=self.settings.docker_compile_image, duration_ms=duration_ms, diagnostics=diagnostics)
        binary_hash = sha256_file(binary)
        metadata = {
            "compile_artifact_id": compile_id, "source_artifact_id": source_artifact_id, "source_sha256": source_sha256,
            "run_id": source_meta["run_id"], "problem_id": problem_id, "compile_profile": compile_profile,
            "compiler_image": self.settings.docker_compile_image, "binary_sha256": binary_hash, "duration_ms": duration_ms,
        }
        atomic_write_json(artifact_root / "metadata.json", metadata)
        return CompileResult(verdict=Verdict.OK, compile_artifact_id=compile_id, source_artifact_id=source_artifact_id, compiler=self.settings.docker_compile_image, duration_ms=duration_ms, diagnostics=diagnostics, binary_sha256=binary_hash)

    def _locate_compile(self, compile_artifact_id: str) -> tuple[Path, dict[str, Any]]:
        ensure(compile_artifact_id.startswith("compile_"), "INVALID_IDENTIFIER", "Invalid compile artifact ID")
        matches = list(self.settings.runs_root.glob(f"*/artifacts/compile/{compile_artifact_id}/metadata.json"))
        ensure(len(matches) == 1, "COMPILE_ARTIFACT_NOT_FOUND", "Compile artifact does not exist", status_code=404)
        metadata = json.loads(matches[0].read_text(encoding="utf-8"))
        binary = matches[0].parent / "main"
        ensure(binary.is_file() and sha256_file(binary) == metadata["binary_sha256"], "COMPILE_ARTIFACT_TAMPERED", "Compiled binary hash mismatch", status_code=500)
        return binary, metadata

    def _run_case(self, binary: Path, compile_meta: dict[str, Any], case: TestCase, problem_id: str, memory_mb: int, memory_source: str, time_ms: int, output_bytes: int) -> TestResult:
        run_root = self.settings.runs_root / compile_meta["run_id"]
        result_root = run_root / "judge_tmp" / safe_id("case")
        result_root.mkdir(parents=True, exist_ok=False)
        result_root.chmod(0o777)
        hard_memory = memory_mb + 32
        timeout_seconds = max(2.0, time_ms / 1000 * 3 + 1)
        args = [
            "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "64", "--cpus", "1",
            "--memory", f"{hard_memory}m", "--memory-swap", f"{hard_memory}m",
            "--tmpfs", "/work:rw,nosuid,size=64m,mode=1777",
            "--mount", f"type=bind,src={binary.resolve()},dst=/artifact/main,readonly",
            "--mount", f"type=bind,src={case.input_path.resolve()},dst=/test/input.in,readonly",
            "--mount", f"type=bind,src={result_root.resolve()},dst=/result",
            self.settings.docker_run_image,
            "/runner", "--executable", "/artifact/main", "--stdin", "/test/input.in",
            "--stdout", "/result/stdout.txt", "--stderr", "/result/stderr.txt", "--stats", "/result/stats.json",
            "--file-output", f"/work/{self.catalog.get(problem_id).io.basename}.out", "--copied-file-output", "/result/file.out",
            "--problem-input", f"/work/{self.catalog.get(problem_id).io.basename}.in", "--time-ms", str(time_ms),
            "--memory-mb", str(memory_mb), "--output-bytes", str(output_bytes),
        ]
        started = time.monotonic()
        try:
            result = self._docker(args, timeout_seconds)
        except ContestLensError:
            shutil.rmtree(result_root, ignore_errors=True)
            return TestResult(test_id=case.test_id, verdict=Verdict.SANDBOX_UNAVAILABLE, memory_limit_mb=memory_mb, memory_limit_source=memory_source, expected_sha256=case.expected_sha256)
        observed_wall = int((time.monotonic() - started) * 1000)
        stats_path = result_root / "stats.json"
        stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.is_file() else {}
        stdout = (result_root / "stdout.txt").read_bytes() if (result_root / "stdout.txt").is_file() else b""
        file_output = (result_root / "file.out").read_bytes() if (result_root / "file.out").is_file() else b""
        stdout_size = len(stdout)
        file_output_size = len(file_output)
        stderr_size = (result_root / "stderr.txt").stat().st_size if (result_root / "stderr.txt").is_file() else 0
        expected = case.expected_path.read_bytes()
        chosen = file_output if file_output else stdout
        verdict = Verdict.AC
        first_diff = None
        equal, first_diff, normalized_expected_hash, actual_hash = compare_noip_fulltext(expected, chosen)
        exit_code = stats.get("exit_code")
        wall_ms = int(stats.get("wall_ms", observed_wall))
        cpu_ms = int(stats.get("cpu_ms", 0))
        peak_rss_mb = round(float(stats.get("peak_rss_kb", 0)) / 1024, 3)
        timed_out = bool(stats.get("timed_out")) or wall_ms > time_ms
        memory_limited = bool(stats.get("memory_limited")) or peak_rss_mb > memory_mb
        output_limited = bool(stats.get("output_limited")) or stdout_size + file_output_size > output_bytes
        if result.returncode != 0 and not stats:
            verdict = Verdict.SANDBOX_VIOLATION
        elif output_limited or stdout_size + file_output_size > output_bytes:
            verdict = Verdict.OLE
        elif memory_limited or peak_rss_mb > memory_mb:
            verdict = Verdict.MLE
        elif timed_out or wall_ms > time_ms:
            verdict = Verdict.TLE
        elif exit_code not in (0, None):
            verdict = Verdict.RE
        elif stdout and file_output and normalize_noip_fulltext(stdout) != normalize_noip_fulltext(file_output):
            verdict = Verdict.IO_CONFLICT
        elif not equal:
            verdict = Verdict.WA
        try:
            shutil.rmtree(result_root)
        except OSError:
            pass
        return TestResult(
            test_id=case.test_id, verdict=verdict, cpu_ms=cpu_ms, wall_ms=wall_ms, peak_rss_mb=peak_rss_mb,
            memory_limit_mb=memory_mb, memory_limit_source=memory_source, expected_sha256=normalized_expected_hash,
            actual_sha256=actual_hash, first_diff=first_diff, exit_code=exit_code,
            termination_signal=termination_signal(exit_code), timed_out=timed_out,
            memory_limited=memory_limited, output_limited=output_limited,
            stdout_bytes=stdout_size, file_output_bytes=file_output_size, stderr_bytes=stderr_size,
        )

    def check_answer(self, compile_artifact_id: str, dataset_id: str, problem_id: str, test_ids: list[str] | None = None) -> CheckResult:
        manifest = self.catalog.get(problem_id)
        ensure(dataset_id == manifest.dataset_id, "DATASET_NOT_FOUND", "Problem does not belong to dataset", status_code=404)
        ensure(not manifest.judge_note, "UNSUPPORTED_COMPARATOR", manifest.judge_note or "Unsupported comparator", status_code=422)
        binary, compile_meta = self._locate_compile(compile_artifact_id)
        ensure(compile_meta["problem_id"] == problem_id, "PROBLEM_ARTIFACT_MISMATCH", "Compile artifact belongs to another problem")
        health = self.healthcheck()
        check_id = safe_id("check")
        if not health["ready"]:
            return CheckResult(check_id=check_id, verdict=Verdict.SANDBOX_UNAVAILABLE, problem_id=problem_id, dataset_id=dataset_id, passed=0, total=0, score=0, tests=[])
        manifest = self.catalog.get(problem_id)
        memory_mb, memory_source = self.catalog.effective_memory(problem_id)
        cases = self.dataset.cases(problem_id)
        if test_ids is not None:
            requested = set(test_ids)
            ensure(requested <= {case.test_id for case in cases}, "TEST_NOT_FOUND", "One or more test IDs do not exist")
            cases = [case for case in cases if case.test_id in requested]
        results = [
            self._run_case(binary, compile_meta, case, problem_id, memory_mb, memory_source, manifest.resource_limits.time_ms, manifest.resource_limits.output_bytes)
            for case in cases
        ]
        passed = sum(item.verdict == Verdict.AC for item in results)
        overall = max((item.verdict for item in results), key=lambda item: VERDICT_PRIORITY[item], default=Verdict.AC)
        check = CheckResult(
            check_id=check_id, verdict=overall, problem_id=problem_id, dataset_id=dataset_id,
            passed=passed, total=len(results), score=passed * manifest.judge.score_per_test, tests=results,
        )
        root = self.settings.runs_root / compile_meta["run_id"] / "artifacts" / "checks" / check_id
        atomic_write_json(root / "result.json", check.model_dump(mode="json"))
        return check

    def get_compile(self, compile_artifact_id: str) -> dict[str, Any]:
        _, metadata = self._locate_compile(compile_artifact_id)
        return metadata

    def get_check(self, check_id: str) -> dict[str, Any]:
        ensure(check_id.startswith("check_"), "INVALID_IDENTIFIER", "Invalid check ID")
        matches = list(self.settings.runs_root.glob(f"*/artifacts/checks/{check_id}/result.json"))
        ensure(len(matches) == 1, "CHECK_NOT_FOUND", "Answer check does not exist", status_code=404)
        return json.loads(matches[0].read_text(encoding="utf-8"))
