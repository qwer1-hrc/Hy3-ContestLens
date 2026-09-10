from types import SimpleNamespace

import pytest

from hy3_contestlens.datasets import ManifestCatalog
from hy3_contestlens.image_understanding import ImageBatchDescriptionResult, ImageDescriptionResult, StatementImages
from hy3_contestlens.resources import ResourceService
from hy3_contestlens.settings import ImageUnderstandingSettings
from hy3_contestlens.store import Store
from hy3_contestlens.utils import sha256_file
from hy3_contestlens.workflow import ContestWorkflow
from hy3_contestlens.workspace import WorkspaceStore


@pytest.mark.asyncio
async def test_localized_markdown_images_reach_vision_model(settings):
    from hy3_contestlens.service import ServiceHub
    settings.resources.roots = [settings.project_root.parent / 'contest_data']
    hub = ServiceHub(settings)
    scope = hub.store.list_scopes()[0]
    pid = 'csps2019_senior_tree'
    found = hub.resources.find_problem_assets(scope['scope_id'], pid)
    candidate = hub.resources.get_candidate(found['auto_selected_candidate_id'])
    binding = hub.resources.bind_candidate(scope['scope_id'], pid, candidate)
    document = hub.resources.read_problem_document(scope['scope_id'], binding['document'])
    calls = []
    async def describe_batch(items):
        calls.append(items)
        return ImageBatchDescriptionResult(
            {item['image_id']: f"{item['label']}：图中节点和边的测试描述" for item in items}, {},
        )
    hub.workflow.image_model = SimpleNamespace(describe_batch=describe_batch)
    hub.resources.settings.image_understanding.api_key = 'mock-key'
    rid = hub.store.create_run(pid, {'image_understanding':'use'})['run_id']
    augmented = await hub.workflow._prepare_images(rid, binding, document, 'use')
    assert len(calls) == 1 and len(calls[0]) == 2
    assert all(item['data_url'].startswith('data:image/png;base64,') for item in calls[0])
    assert all(len(item['context']) < len(document['content']) for item in calls[0])
    assert len(augmented['visual_descriptions']) == 2
    state = hub.store.get_run(rid)['image_understanding']
    assert state['status'] == 'completed'
    assert state['warnings'] == []


@pytest.mark.asyncio
async def test_real_game_pdf_pages_are_rendered_described_and_injected(settings):
    settings.image_understanding = ImageUnderstandingSettings(api_key="mock-vision-key")
    store = Store(settings.database_path)
    resources = ResourceService(settings, store, ManifestCatalog(settings.manifests_root))
    scope = resources.grant_host_path(str(settings.resources.roots[0]), True, configured_root=True)
    pdf = settings.resources.roots[0] / "NOIP2018FinalSeniorDay2TestPaper.pdf"
    document_binding = {
        "document_id": "game-pdf", "relative_path": pdf.name, "sha256": sha256_file(pdf),
        "page_start": 4, "page_end": 6,
    }
    document = resources.read_problem_document(scope["scope_id"], document_binding)
    calls = []

    async def describe(data_url, label, context):
        calls.append({"data_url": data_url, "label": label, "context": context})
        return ImageDescriptionResult(
            f"{label} 的模拟视觉描述：包含路径、网格和数字。",
            {"http_status": 200, "finish_reason": "stop", "usage": {"total_tokens": 20}},
        )

    workflow = ContestWorkflow(
        store, resources, WorkspaceStore(settings), None, None, None,
        SimpleNamespace(describe=describe),
    )
    run_id = store.create_run("game", {"image_understanding": "use"})["run_id"]
    store.update_run(run_id, "ANALYZING")
    augmented = await workflow._prepare_images(
        run_id, {"scope_id": scope["scope_id"], "document": document_binding}, document, "use",
    )

    assert [call["label"] for call in calls] == ["PDF 第 4 页", "PDF 第 5 页"]
    assert all(call["data_url"].startswith("data:image/png;base64,") for call in calls)
    assert all("填数游戏" in call["context"] for call in calls)
    assert augmented["content"].startswith(document["content"])
    assert "[UNTRUSTED IMAGE DESCRIPTIONS / 图片理解补充]" in augmented["content"]
    assert [item["source_label"] for item in augmented["visual_descriptions"]] == ["PDF 第 4 页", "PDF 第 5 页"]
    state = store.get_run(run_id)["image_understanding"]
    assert state["status"] == "completed" and not state["warnings"]
    assert state["descriptions"][0]["diagnostics"]["usage"]["total_tokens"] == 20
    assert "base64" not in str(state)
    assert any(event["type"] == "IMAGE_UNDERSTANDING_COMPLETED" for event in store.list_events(run_id))
