from hy3_contestlens.datasets import ManifestCatalog, PrivateDataset
from hy3_contestlens.domain import Verdict
from hy3_contestlens.judge import DockerJudge
from hy3_contestlens.workspace import WorkspaceStore


def test_compile_refuses_to_fallback_when_sandbox_is_unavailable(settings, monkeypatch):
    workspace = WorkspaceStore(settings)
    source = (
        '#include <cstdio>\n'
        'int main(){freopen("road.in", "r", stdin); freopen("road.out", "w", stdout); return 0;}\n'
    )
    created = workspace.create_cpp_submission("run_secure", "road", source)
    frozen = workspace.freeze_cpp_revision("run_secure", created["submission_id"], "r000", created["sha256"])
    judge = DockerJudge(settings, ManifestCatalog(settings.manifests_root), PrivateDataset(settings.private_data_root, ManifestCatalog(settings.manifests_root)), workspace)
    monkeypatch.setattr(judge, "healthcheck", lambda: {"ready": False, "message": "not running"})
    monkeypatch.setattr(judge, "_docker", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("host fallback or docker call occurred")))
    result = judge.compile_cpp("road", frozen["source_artifact_id"], frozen["source_sha256"])
    assert result.verdict == Verdict.SANDBOX_UNAVAILABLE
