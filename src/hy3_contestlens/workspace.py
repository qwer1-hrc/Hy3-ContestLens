from __future__ import annotations

import difflib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .errors import ContestLensError, ensure
from .settings import AppSettings
from .utils import atomic_write, atomic_write_json, canonical_json, safe_id, sha256_bytes, sha256_file, utc_now


SAFE_ID = re.compile(r"^[A-Za-z0-9_]{3,80}$")
REVISION = re.compile(r"^r\d{3,6}$")
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _without_cpp_comments(source: str) -> str:
    return re.sub(r"//[^\n]*|/\*.*?\*/", "", source, flags=re.DOTALL)


def validate_problem_file_io(source: str, problem_id: str) -> None:
    active_source = _without_cpp_comments(source)
    basename = re.escape(problem_id)
    input_call = re.compile(
        rf'(?:std::)?freopen\s*\(\s*"{basename}\.in"\s*,\s*"r[b]?"\s*,\s*stdin\s*\)'
    )
    output_call = re.compile(
        rf'(?:std::)?freopen\s*\(\s*"{basename}\.out"\s*,\s*"w[b]?"\s*,\s*stdout\s*\)'
    )
    missing: list[str] = []
    if input_call.search(active_source) is None:
        missing.append(f'freopen("{problem_id}.in", "r", stdin)')
    if output_call.search(active_source) is None:
        missing.append(f'freopen("{problem_id}.out", "w", stdout)')
    ensure(
        not missing,
        "REQUIRED_FILE_IO_MISSING",
        "C++ source must use the problem-specific file input and output required by the judge",
        problem_id=problem_id,
        missing=missing,
    )


def validate_cpp(source: str, max_bytes: int, problem_id: str | None = None) -> bytes:
    ensure("\x00" not in source, "INVALID_CPP_SOURCE", "C++ source contains a NUL byte")
    data = source.encode("utf-8")
    ensure(len(data) <= max_bytes, "CPP_SOURCE_TOO_LARGE", "C++ source exceeds the configured limit", size_bytes=len(data), max_bytes=max_bytes)
    if problem_id is not None:
        validate_problem_file_io(source, problem_id)
    return data


def apply_unified_diff(source: str, patch: str) -> str:
    ensure("\x00" not in patch, "INVALID_PATCH", "Patch contains a NUL byte")
    source_lines = source.splitlines(keepends=True)
    patch_lines = patch.splitlines(keepends=True)
    index = 0
    while index < len(patch_lines) and not patch_lines[index].startswith("@@"):
        line = patch_lines[index]
        if line.startswith(("--- ", "+++ ")):
            name = line[4:].strip().split("\t", 1)[0]
            ensure(name in {"a/main.cpp", "b/main.cpp", "main.cpp"}, "PATCH_PATH_DENIED", "Patch may only target main.cpp", path=name)
        index += 1
    output: list[str] = []
    source_index = 0
    found_hunk = False
    while index < len(patch_lines):
        match = HUNK.match(patch_lines[index].rstrip("\r\n"))
        ensure(match is not None, "INVALID_PATCH", "Expected a unified diff hunk header")
        found_hunk = True
        old_start = int(match.group(1)) - 1
        ensure(old_start >= source_index, "PATCH_CONFLICT", "Patch hunks overlap or are out of order")
        output.extend(source_lines[source_index:old_start])
        source_index = old_start
        index += 1
        while index < len(patch_lines) and not patch_lines[index].startswith("@@"):
            line = patch_lines[index]
            if line.startswith("\\ No newline at end of file"):
                index += 1
                continue
            ensure(line[:1] in {" ", "+", "-"}, "INVALID_PATCH", "Invalid unified diff line")
            content = line[1:]
            if line.startswith(" "):
                ensure(source_index < len(source_lines) and source_lines[source_index] == content, "PATCH_CONFLICT", "Patch context does not match source")
                output.append(content)
                source_index += 1
            elif line.startswith("-"):
                ensure(source_index < len(source_lines) and source_lines[source_index] == content, "PATCH_CONFLICT", "Patch deletion does not match source")
                source_index += 1
            else:
                output.append(content)
            index += 1
    ensure(found_hunk, "INVALID_PATCH", "Patch contains no hunks")
    output.extend(source_lines[source_index:])
    return "".join(output)


class WorkspaceStore:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.root = settings.runs_root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _id(value: str, label: str) -> str:
        ensure(bool(SAFE_ID.fullmatch(value)), "INVALID_IDENTIFIER", f"Invalid {label}", **{label: value})
        return value

    def _run(self, run_id: str) -> Path:
        return self.root / self._id(run_id, "run_id")

    def _submission(self, run_id: str, submission_id: str) -> Path:
        return self._run(run_id) / "workspace" / "submissions" / self._id(submission_id, "submission_id")

    def _metadata_path(self, run_id: str, submission_id: str) -> Path:
        return self._submission(run_id, submission_id) / "metadata.json"

    def _load(self, run_id: str, submission_id: str) -> dict[str, Any]:
        path = self._metadata_path(run_id, submission_id)
        if not path.is_file():
            raise ContestLensError("SUBMISSION_NOT_FOUND", "Submission does not exist", {"submission_id": submission_id}, 404)
        return json.loads(path.read_text(encoding="utf-8"))

    def _audit(self, run_id: str, action: str, data: dict[str, Any]) -> str:
        audit_id = safe_id("audit")
        path = self._run(run_id) / "workspace" / "workspace_audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"audit_id": audit_id, "action": action, "created_at": utc_now(), **data}
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_json(record) + "\n")
        return audit_id

    def create_cpp_submission(self, run_id: str, problem_id: str, source_code: str, created_by: str = "solver") -> dict[str, Any]:
        self._id(run_id, "run_id")
        ensure(re.fullmatch(r"^[a-z][a-z0-9_]*$", problem_id) is not None, "INVALID_PROBLEM_ID", "Invalid problem ID")
        data = validate_cpp(source_code, self.settings.max_source_bytes, problem_id)
        submission_id = safe_id("submission")
        directory = self._submission(run_id, submission_id)
        ensure(not directory.exists(), "SUBMISSION_CONFLICT", "Submission already exists", status_code=409)
        revision_id = "r000"
        sha256 = sha256_bytes(data)
        created_at = utc_now()
        atomic_write(directory / "revisions" / f"{revision_id}.cpp", data)
        metadata = {
            "schema_version": 1, "run_id": run_id, "submission_id": submission_id, "problem_id": problem_id,
            "revisions": [{
                "revision_id": revision_id, "parent_revision_id": None, "sha256": sha256, "size_bytes": len(data),
                "created_by": created_by, "created_at": created_at, "repair_round": 0, "repair_plan_id": None,
                "reason": "initial generation", "change_kind": "create", "frozen_artifacts": [],
            }],
        }
        atomic_write_json(self._metadata_path(run_id, submission_id), metadata)
        audit_id = self._audit(run_id, "create_cpp_submission", {"submission_id": submission_id, "revision_id": revision_id, "sha256": sha256, "created_by": created_by})
        return {"run_id": run_id, "problem_id": problem_id, "submission_id": submission_id, "revision_id": revision_id, "sha256": sha256, "size_bytes": len(data), "audit_id": audit_id}

    def _revision(self, metadata: dict[str, Any], revision_id: str) -> dict[str, Any]:
        ensure(bool(REVISION.fullmatch(revision_id)), "INVALID_REVISION_ID", "Invalid revision ID")
        revision = next((item for item in metadata["revisions"] if item["revision_id"] == revision_id), None)
        if not revision:
            raise ContestLensError("REVISION_NOT_FOUND", "Revision does not exist", {"revision_id": revision_id}, 404)
        return revision

    def read_cpp_submission(self, run_id: str, submission_id: str, revision_id: str) -> dict[str, Any]:
        metadata = self._load(run_id, submission_id)
        revision = self._revision(metadata, revision_id)
        source_path = self._submission(run_id, submission_id) / "revisions" / f"{revision_id}.cpp"
        source = source_path.read_text(encoding="utf-8")
        ensure(sha256_file(source_path) == revision["sha256"], "REVISION_TAMPERED", "Revision content no longer matches its immutable metadata", status_code=500)
        return {"run_id": run_id, "submission_id": submission_id, "problem_id": metadata["problem_id"], **revision, "source_code": source}

    def list_cpp_revisions(self, run_id: str, submission_id: str) -> dict[str, Any]:
        metadata = self._load(run_id, submission_id)
        return {"run_id": run_id, "submission_id": submission_id, "problem_id": metadata["problem_id"], "revisions": metadata["revisions"]}

    def get_cpp_metadata(self, run_id: str, submission_id: str, revision_id: str) -> dict[str, Any]:
        metadata = self._load(run_id, submission_id)
        return {"run_id": run_id, "submission_id": submission_id, "problem_id": metadata["problem_id"], **self._revision(metadata, revision_id)}

    def _next_revision(self, metadata: dict[str, Any]) -> str:
        number = max(int(item["revision_id"][1:]) for item in metadata["revisions"]) + 1
        return f"r{number:03d}"

    def _commit_revision(self, run_id: str, submission_id: str, base_revision_id: str, base_sha256: str, source_code: str, *, created_by: str, repair_round: int, repair_plan_id: str, reason: str, change_kind: str, normalized_diff: str) -> dict[str, Any]:
        metadata = self._load(run_id, submission_id)
        base = self._revision(metadata, base_revision_id)
        ensure(base["sha256"] == base_sha256, "STALE_REVISION", "base_sha256 does not match the selected revision", status_code=409, expected=base["sha256"], actual=base_sha256)
        data = validate_cpp(source_code, self.settings.max_source_bytes, metadata["problem_id"])
        revision_id = self._next_revision(metadata)
        digest = sha256_bytes(data)
        ensure(digest != base_sha256, "NO_SOURCE_CHANGE", "New revision must change the source")
        revision = {
            "revision_id": revision_id, "parent_revision_id": base_revision_id, "sha256": digest, "size_bytes": len(data),
            "created_by": created_by, "created_at": utc_now(), "repair_round": repair_round, "repair_plan_id": repair_plan_id,
            "reason": reason, "change_kind": change_kind, "frozen_artifacts": [], "diff": normalized_diff,
        }
        atomic_write(self._submission(run_id, submission_id) / "revisions" / f"{revision_id}.cpp", data)
        metadata["revisions"].append(revision)
        atomic_write_json(self._metadata_path(run_id, submission_id), metadata)
        audit_id = self._audit(run_id, f"{change_kind}_cpp_submission", {"submission_id": submission_id, "base_revision_id": base_revision_id, "base_sha256": base_sha256, "revision_id": revision_id, "sha256": digest, "created_by": created_by, "repair_round": repair_round})
        return {"run_id": run_id, "submission_id": submission_id, "problem_id": metadata["problem_id"], **revision, "audit_id": audit_id}

    def replace_cpp_submission(self, run_id: str, submission_id: str, base_revision_id: str, base_sha256: str, source_code: str, repair_round: int, repair_plan_id: str, reason: str, created_by: str = "code_repair_agent") -> dict[str, Any]:
        ensure(self.get_cpp_metadata(run_id, submission_id, base_revision_id)["sha256"] == base_sha256, "STALE_REVISION", "base_sha256 does not match the selected revision", status_code=409)
        old = self.read_cpp_submission(run_id, submission_id, base_revision_id)["source_code"]
        diff = "".join(difflib.unified_diff(old.splitlines(keepends=True), source_code.splitlines(keepends=True), fromfile="a/main.cpp", tofile="b/main.cpp"))
        return self._commit_revision(run_id, submission_id, base_revision_id, base_sha256, source_code, created_by=created_by, repair_round=repair_round, repair_plan_id=repair_plan_id, reason=reason, change_kind="replace", normalized_diff=diff)

    def apply_cpp_patch(self, run_id: str, submission_id: str, base_revision_id: str, base_sha256: str, patch: str, repair_round: int, repair_plan_id: str, reason: str, created_by: str = "code_repair_agent") -> dict[str, Any]:
        ensure(self.get_cpp_metadata(run_id, submission_id, base_revision_id)["sha256"] == base_sha256, "STALE_REVISION", "base_sha256 does not match the selected revision", status_code=409)
        old = self.read_cpp_submission(run_id, submission_id, base_revision_id)["source_code"]
        new = apply_unified_diff(old, patch)
        normalized = "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), fromfile="a/main.cpp", tofile="b/main.cpp"))
        return self._commit_revision(run_id, submission_id, base_revision_id, base_sha256, new, created_by=created_by, repair_round=repair_round, repair_plan_id=repair_plan_id, reason=reason, change_kind="patch", normalized_diff=normalized)

    def freeze_cpp_revision(self, run_id: str, submission_id: str, revision_id: str, expected_sha256: str) -> dict[str, Any]:
        metadata = self._load(run_id, submission_id)
        revision = self._revision(metadata, revision_id)
        ensure(revision["sha256"] == expected_sha256, "SOURCE_HASH_MISMATCH", "Expected source hash does not match revision", status_code=409)
        source_path = self._submission(run_id, submission_id) / "revisions" / f"{revision_id}.cpp"
        ensure(sha256_file(source_path) == expected_sha256, "REVISION_TAMPERED", "Revision content no longer matches metadata", status_code=500)
        artifact_id = safe_id("source")
        artifact_root = self._run(run_id) / "artifacts" / "source" / artifact_id
        target = artifact_root / "main.cpp"
        target.parent.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(source_path, target)
        target.chmod(stat_mode_read_only(target))
        artifact = {
            "source_artifact_id": artifact_id, "run_id": run_id, "submission_id": submission_id, "revision_id": revision_id,
            "problem_id": metadata["problem_id"], "source_sha256": expected_sha256, "created_at": utc_now(),
        }
        atomic_write_json(artifact_root / "metadata.json", artifact)
        revision["frozen_artifacts"].append(artifact_id)
        atomic_write_json(self._metadata_path(run_id, submission_id), metadata)
        artifact["audit_id"] = self._audit(run_id, "freeze_cpp_revision", artifact)
        return artifact

    def locate_source_artifact(self, artifact_id: str) -> tuple[Path, dict[str, Any]]:
        self._id(artifact_id, "source_artifact_id")
        matches = list(self.root.glob(f"*/artifacts/source/{artifact_id}/metadata.json"))
        ensure(len(matches) == 1, "SOURCE_ARTIFACT_NOT_FOUND", "Frozen source artifact does not exist", status_code=404)
        metadata = json.loads(matches[0].read_text(encoding="utf-8"))
        source_path = matches[0].parent / "main.cpp"
        ensure(source_path.is_file() and sha256_file(source_path) == metadata["source_sha256"], "SOURCE_ARTIFACT_TAMPERED", "Frozen source artifact hash mismatch", status_code=500)
        return source_path, metadata


def stat_mode_read_only(path: Path) -> int:
    return path.stat().st_mode & ~0o222
