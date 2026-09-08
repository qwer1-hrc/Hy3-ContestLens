from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from hy3_contestlens.api.app import create_app
from hy3_contestlens.datasets import ManifestCatalog, PrivateDataset, validate_import
from hy3_contestlens.errors import ContestLensError


def test_collection_identity_and_structure(settings):
    catalog = ManifestCatalog(settings.manifests_root)
    problems = catalog.list()
    assert len(problems) == 74
    assert len({m.problem_id for m in problems}) == 74
    assert len({m.luogu_id for m in problems}) == 74
    assert catalog.get('road').dataset_id == 'noip2018'
    assert catalog.get('noip2014_senior_road').io.basename == 'road'
    assert catalog.get('csps2025_senior_road').io.basename == 'road'
    assert sum(m.contest == 'CSP-S' and m.year == 2019 for m in problems) == 6
    assert all(m.group == 'senior' for m in problems if m.contest == 'NOIP' and m.year >= 2020)
    for invalid in ['../road', '/road', 'noip2014_senior/road', 'unknown']:
        with pytest.raises(ContestLensError):
            catalog.get(invalid)


def test_import_and_missing_data_are_explicit(settings):
    catalog = ManifestCatalog(settings.manifests_root)
    result = validate_import(settings.private_data_root, catalog)
    assert result['valid']
    assert result['missing_data'] == []
    assert result['count'] == 1200
    for basename, count in [('julian',10), ('zoo',20), ('call',20), ('snakes',20)]:
        manifest = catalog.get('csps2020_senior_' + basename)
        assert manifest.test_count == count
        assert manifest.data_status == 'official'
        assert manifest.judge_note is None
    for m in catalog.list():
        assert m.luogu_difficulty and m.difficulty_checked_at
        if m.year == 2025:
            assert m.data_status == 'samples'
        if m.data_status != 'missing':
            assert len(PrivateDataset(settings.private_data_root, catalog).cases(m.problem_id)) == m.test_count


def test_exact_statement_binding_and_safe_problem_page(settings):
    root = settings.project_root.parent / 'contest_data'
    settings.resources.roots = [root]
    client = TestClient(create_app(settings))
    hub = client.app.state.hub
    scope_id = hub.store.list_scopes()[0]['scope_id']
    pid = 'noip2014_senior_road'
    found = hub.resources.find_problem_assets(scope_id, pid)
    assert found['status'] == 'AUTO_BINDABLE'
    candidate = hub.resources.get_candidate(found['auto_selected_candidate_id'])
    hub.resources.bind_candidate(scope_id, pid, candidate)
    text = client.get(f'/api/v1/problems/{pid}/statement').json()['content']
    assert '寻找道路' in text and '数据范围' in text
    assert 'tests/' not in text and 'std/' not in text
    assert client.get(f'/ui/problems/{pid}').status_code == 200
    csp_page = client.get('/ui/problems/csps2020_senior_julian')
    assert csp_page.status_code == 200 and '开始评测' in csp_page.text
    assert client.get('/api/v1/problems').status_code == 200
    assert len(client.get('/api/v1/problems').json()) == 74
    assert len(client.get('/api/v1/datasets/noip2018/problems').json()) == 6
    assert client.get('/api/v1/datasets/absent/problems').status_code == 404
    for problem_id in ['noip2020_senior_ball', 'noip2022_senior_meow']:
        assert client.post('/api/v1/runs', json={'problem_id':problem_id}).status_code == 422


def test_namespaced_problem_uses_original_file_io(settings, monkeypatch):
    from hy3_contestlens.service import ServiceHub
    hub = ServiceHub(settings)
    pid = 'noip2014_senior_road'
    case = hub.dataset.cases(pid)[0]
    captured = []
    def fake_docker(args, timeout):
        captured.extend(args)
        raise ContestLensError('DOCKER_UNAVAILABLE', 'Test stub')
    monkeypatch.setattr(hub.judge, '_docker', fake_docker)
    hub.judge._run_case(Path('fake-binary'), {'run_id':'run_io'}, case, pid, 128, 'problem_manifest', 1000, 1024)
    assert captured[captured.index('--problem-input')+1] == '/work/road.in'
    assert captured[captured.index('--file-output')+1] == '/work/road.out'
