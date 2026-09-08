from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .domain import ProblemManifest
from .errors import ContestLensError, ensure
from .utils import atomic_write_json, sha256_file, utc_now


PROBLEM_IDS = ("road", "money", "track", "travel", "game", "defense")


@dataclass(frozen=True, slots=True)
class TestCase:
    test_id: str
    input_path: Path
    expected_path: Path
    input_sha256: str
    expected_sha256: str


class ManifestCatalog:
    def __init__(self, manifests_root: Path, default_memory_mb: int = 512):
        self.root = manifests_root
        self.default_memory_mb = default_memory_mb

    def get(self, problem_id: str) -> ProblemManifest:
        paths = {path.stem: path for path in self.root.glob("*/*.json") if path.name != "difficulty.json"}
        ensure(problem_id in paths, "PROBLEM_NOT_FOUND", "Unknown contest problem", status_code=404, problem_id=problem_id)
        try:
            data = json.loads(paths[problem_id].read_text(encoding="utf-8"))
            return ProblemManifest.model_validate(data)
        except FileNotFoundError as exc:
            raise ContestLensError("PROBLEM_MANIFEST_NOT_FOUND", "Problem manifest is missing", {"problem_id": problem_id}, 500) from exc
        except Exception as exc:
            raise ContestLensError("INVALID_PROBLEM_MANIFEST", "Problem manifest is invalid", {"problem_id": problem_id, "reason": str(exc)}, 500) from exc

    def list(self) -> list[ProblemManifest]:
        ids = [path.stem for path in self.root.glob("*/*.json") if path.name != "difficulty.json"]
        ensure(len(ids) == len(set(ids)), "INVALID_PROBLEM_MANIFEST", "Duplicate problem IDs")
        return sorted([self.get(pid) for pid in ids], key=lambda m: (m.contest, m.year, m.group, m.day, m.task_number, m.problem_id))

    def effective_memory(self, problem_id: str) -> tuple[int, str]:
        manifest = self.get(problem_id)
        value = manifest.resource_limits.memory_mb
        if value is None:
            return self.default_memory_mb, "default"
        return value, "problem_manifest"


def natural_test_key(path: Path) -> tuple[str, int]:
    stem = path.stem
    suffix = "".join(character for character in stem if character.isdigit())
    prefix = stem[: len(stem) - len(suffix)]
    return prefix, int(suffix or 0)


class PrivateDataset:
    def __init__(self, private_root: Path, catalog: ManifestCatalog):
        self.root = private_root
        self.catalog = catalog

    def cases(self, problem_id: str) -> list[TestCase]:
        manifest = self.catalog.get(problem_id)
        problem_root = self.root / manifest.dataset_id / f"day{manifest.day}" / problem_id
        inputs = sorted((problem_root / "tests").glob("*.in"), key=natural_test_key)
        cases: list[TestCase] = []
        for input_path in inputs:
            expected = problem_root / "expected" / f"{input_path.stem}.out"
            if not expected.is_file():
                raise ContestLensError("PRIVATE_DATASET_INCOMPLETE", "Expected output is missing", {"test_id": input_path.stem}, 500)
            cases.append(TestCase(input_path.stem, input_path, expected, sha256_file(input_path), sha256_file(expected)))
        if not cases:
            raise ContestLensError("PRIVATE_DATASET_NOT_IMPORTED", "Private contest data has not been imported", {"problem_id": problem_id}, 503)
        return cases

    def summary(self, problem_id: str) -> dict[str, Any]:
        try:
            cases = self.cases(problem_id)
        except ContestLensError as exc:
            if exc.code == "PRIVATE_DATASET_NOT_IMPORTED":
                return {"problem_id": problem_id, "count": 0, "imported": False}
            raise
        return {"problem_id": problem_id, "count": len(cases), "imported": True, "test_ids": [case.test_id for case in cases]}


def source_problem_dir(source_root: Path, manifest: ProblemManifest) -> Path:
    if manifest.statement_relative_path:
        return source_root / Path(manifest.statement_relative_path).parent / "tests"
    return source_root / "test_data" / f"day{manifest.day}_data" / manifest.problem_id


def import_noip2018(source_root: Path, private_root: Path, catalog: ManifestCatalog) -> dict[str, Any]:
    source_root = source_root.resolve()
    ensure(source_root.is_dir(), "SOURCE_DATASET_NOT_FOUND", "NOIP2018 source directory does not exist", path=str(source_root))
    records: list[dict[str, Any]] = []
    for manifest in (catalog.get(pid) for pid in PROBLEM_IDS):
        source = source_root / "test_data" / f"day{manifest.day}_data" / manifest.problem_id
        ensure(source.is_dir(), "SOURCE_PROBLEM_NOT_FOUND", "Problem source directory does not exist", problem_id=manifest.problem_id)
        inputs = sorted(source.glob(f"{manifest.problem_id}*.in"), key=natural_test_key)
        answers = {path.stem: path for path in source.glob(f"{manifest.problem_id}*.ans")}
        ensure(len(inputs) == len(answers), "SOURCE_DATASET_INCOMPLETE", "Input/answer count differs", problem_id=manifest.problem_id, inputs=len(inputs), answers=len(answers))
        destination = private_root / "noip2018" / f"day{manifest.day}" / manifest.problem_id
        for input_path in inputs:
            answer_path = answers.get(input_path.stem)
            ensure(answer_path is not None, "SOURCE_DATASET_INCOMPLETE", "Answer is missing", test_id=input_path.stem)
            destination_input = destination / "tests" / input_path.name
            destination_output = destination / "expected" / f"{input_path.stem}.out"
            destination_input.parent.mkdir(parents=True, exist_ok=True)
            destination_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(input_path, destination_input)
            shutil.copy2(answer_path, destination_output)
            records.append({
                "problem_id": manifest.problem_id,
                "test_id": input_path.stem,
                "source_input_sha256": sha256_file(input_path),
                "source_answer_sha256": sha256_file(answer_path),
                "imported_input_sha256": sha256_file(destination_input),
                "imported_output_sha256": sha256_file(destination_output),
                "answer_mapping": ".ans -> .out",
            })
    summary = {
        "schema_version": 1,
        "dataset_id": "noip2018",
        "source_display_name": source_root.name,
        "created_at": utc_now(),
        "test_count": len(records),
        "problems": {problem_id: sum(item["problem_id"] == problem_id for item in records) for problem_id in PROBLEM_IDS},
        "tests": records,
    }
    atomic_write_json(private_root / "noip2018" / "import_manifest.json", summary)
    return summary


def validate_import(private_root: Path, catalog: ManifestCatalog, dataset_id: str | None = None) -> dict[str, Any]:
    dataset = PrivateDataset(private_root, catalog)
    manifests = [m for m in catalog.list() if dataset_id is None or m.dataset_id == dataset_id]
    summaries = [dataset.summary(m.problem_id) for m in manifests]
    count = sum(item["count"] for item in summaries)
    valid = bool(manifests) and all((s["imported"] or m.data_status == "missing") and (m.test_count is None or s["count"] == m.test_count) for m,s in zip(manifests,summaries))
    return {"valid": valid, "count": count, "missing_data": [m.problem_id for m in manifests if m.data_status == "missing"], "problems": summaries}
