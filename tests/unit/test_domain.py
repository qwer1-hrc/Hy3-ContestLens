from __future__ import annotations

import pytest
from pydantic import ValidationError

from hy3_contestlens.domain import ProblemManifest


def manifest(memory):
    return {
        "schema_version": 1, "dataset_id": "noip2018", "problem_id": "road", "title_zh": "铺设道路", "day": 1,
        "luogu_difficulty": None, "resource_limits": {"time_ms": 1000, "memory_mb": memory, "output_bytes": 1024},
        "io": {"basename": "road", "input_mode": "stdin_and_file", "output_mode": "stdout_or_file"},
        "judge": {"comparator": "noip_fulltext", "input_glob": "tests/*.in", "expected_glob": "expected/*.out", "score_per_test": 10},
    }


def test_memory_accepts_positive_integer_and_null():
    assert ProblemManifest.model_validate(manifest(512)).resource_limits.memory_mb == 512
    assert ProblemManifest.model_validate(manifest(None)).resource_limits.memory_mb is None


@pytest.mark.parametrize("value", [0, -1, "512", 512.0, True])
def test_memory_rejects_invalid_values_instead_of_falling_back(value):
    with pytest.raises(ValidationError):
        ProblemManifest.model_validate(manifest(value))

