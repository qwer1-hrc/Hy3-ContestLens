from hy3_contestlens.datasets import ManifestCatalog, validate_import
import json
from pathlib import Path


def test_private_import_has_all_120_pairs(settings):
    result = validate_import(settings.private_data_root, ManifestCatalog(settings.manifests_root), "noip2018")
    assert result["valid"] is True
    assert result["count"] == 120
    assert [item["count"] for item in result["problems"]] == [10, 20, 20, 25, 20, 25]


def test_process_case_plan_has_72_explicit_unexecuted_templates(settings):
    path = settings.project_root / "data" / "process_cases" / "noip2018_process_cases.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 72
    assert {item["problem_id"] for item in records} == {"road", "money", "track", "travel", "game", "defense"}
    assert all(item["validation_status"] == "TEMPLATE_NOT_EXECUTED" for item in records)
