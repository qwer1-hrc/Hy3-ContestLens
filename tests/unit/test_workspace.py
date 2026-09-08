from __future__ import annotations

import difflib

import pytest

from hy3_contestlens.errors import ContestLensError
from hy3_contestlens.workspace import WorkspaceStore


SOURCE = "#include <cstdio>\n#include <iostream>\nint main(){freopen(\"road.in\", \"r\", stdin); freopen(\"road.out\", \"w\", stdout); std::cout << 1 << '\\n';}\n"
FIXED = "#include <cstdio>\n#include <iostream>\nint main(){freopen(\"road.in\", \"r\", stdin); freopen(\"road.out\", \"w\", stdout); std::cout << 2 << '\\n';}\n"


@pytest.mark.parametrize('problem_id,basename', [
    ('csps2019_senior_meal', 'meal'),
    ('csps2019_senior_brackets', 'brackets'),
    ('noip2014_senior_road', 'road'),
])
def test_catalog_basename_used_for_create_recovery_and_repair(settings, problem_id, basename):
    workspace = WorkspaceStore(settings)
    source = SOURCE.replace('road.', basename + '.')
    fixed = FIXED.replace('road.', basename + '.')
    run_id = 'run_basename'
    created = workspace.get_or_create_cpp_submission(run_id, problem_id, source)
    recovered = workspace.get_or_create_cpp_submission(run_id, problem_id, source)
    assert recovered['submission_id'] == created['submission_id']
    assert recovered['recovered']
    revised = workspace.replace_cpp_submission(run_id, created['submission_id'], 'r000', created['sha256'], fixed, 1, 'plan_basename', 'test')
    frozen = workspace.freeze_cpp_revision(run_id, created['submission_id'], revised['revision_id'], revised['sha256'])
    path, _ = workspace.locate_source_artifact(frozen['source_artifact_id'])
    assert path.read_text(encoding='utf-8') == fixed
    wrong = source.replace(basename + '.', problem_id + '.')
    with pytest.raises(ContestLensError) as error:
        workspace.create_cpp_submission('run_wrong_basename', problem_id, wrong)
    assert error.value.code == 'REQUIRED_FILE_IO_MISSING'


def test_revision_patch_stale_hash_and_freeze(settings):
    workspace = WorkspaceStore(settings)
    created = workspace.create_cpp_submission("run_test", "road", SOURCE)
    diff = "".join(difflib.unified_diff(SOURCE.splitlines(keepends=True), FIXED.splitlines(keepends=True), fromfile="a/main.cpp", tofile="b/main.cpp"))
    patched = workspace.apply_cpp_patch("run_test", created["submission_id"], "r000", created["sha256"], diff, 1, "plan_test", "fix output")
    assert patched["revision_id"] == "r001"
    assert workspace.read_cpp_submission("run_test", created["submission_id"], "r000")["source_code"] == SOURCE
    assert workspace.read_cpp_submission("run_test", created["submission_id"], "r001")["source_code"] == FIXED
    with pytest.raises(ContestLensError) as error:
        workspace.apply_cpp_patch("run_test", created["submission_id"], "r001", created["sha256"], diff, 2, "plan_test2", "stale")
    assert error.value.code == "STALE_REVISION"
    frozen = workspace.freeze_cpp_revision("run_test", created["submission_id"], "r001", patched["sha256"])
    path, metadata = workspace.locate_source_artifact(frozen["source_artifact_id"])
    assert metadata["source_sha256"] == patched["sha256"]
    assert path.read_text(encoding="utf-8") == FIXED


def test_patch_cannot_target_another_file(settings):
    workspace = WorkspaceStore(settings)
    created = workspace.create_cpp_submission("run_test2", "road", SOURCE)
    malicious = "--- a/main.cpp\n+++ b/steal.txt\n@@ -1,1 +1,1 @@\n-x\n+y\n"
    with pytest.raises(ContestLensError) as error:
        workspace.apply_cpp_patch("run_test2", created["submission_id"], "r000", created["sha256"], malicious, 1, "plan_x", "bad")
    assert error.value.code == "PATCH_PATH_DENIED"


def test_submission_requires_active_problem_specific_freopen(settings):
    workspace = WorkspaceStore(settings)
    missing = "#include <iostream>\nint main(){std::cout << 1 << '\\n';}\n"
    with pytest.raises(ContestLensError) as error:
        workspace.create_cpp_submission("run_file_io", "road", missing)
    assert error.value.code == "REQUIRED_FILE_IO_MISSING"

    commented = (
        "#include <cstdio>\n"
        "// freopen(\"road.in\", \"r\", stdin);\n"
        "// freopen(\"road.out\", \"w\", stdout);\n"
        "int main(){return 0;}\n"
    )
    with pytest.raises(ContestLensError) as error:
        workspace.create_cpp_submission("run_commented_io", "road", commented)
    assert error.value.code == "REQUIRED_FILE_IO_MISSING"


def test_repair_revision_cannot_remove_required_freopen(settings):
    workspace = WorkspaceStore(settings)
    created = workspace.create_cpp_submission("run_keep_file_io", "road", SOURCE)
    without_file_io = "#include <iostream>\nint main(){std::cout << 2 << '\\n';}\n"
    with pytest.raises(ContestLensError) as error:
        workspace.replace_cpp_submission(
            "run_keep_file_io",
            created["submission_id"],
            "r000",
            created["sha256"],
            without_file_io,
            1,
            "plan_keep_io",
            "invalid removal",
        )
    assert error.value.code == "REQUIRED_FILE_IO_MISSING"
