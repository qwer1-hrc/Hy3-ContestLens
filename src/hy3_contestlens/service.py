from __future__ import annotations

import asyncio

from .datasets import ManifestCatalog, PrivateDataset
from .judge import DockerJudge
from .image_understanding import ImageUnderstandingClient
from .model import Hy3Client
from .report_translation import ReportTranslationService
from .resources import ResourceService
from .settings import AppSettings
from .store import Store
from .workflow import ContestWorkflow
from .workspace import WorkspaceStore


class ServiceHub:
    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or AppSettings.load()
        self.store = Store(self.settings.database_path)
        self.catalog = ManifestCatalog(self.settings.manifests_root, self.settings.default_memory_mb)
        self.dataset = PrivateDataset(self.settings.private_data_root, self.catalog)
        self.resources = ResourceService(self.settings, self.store, self.catalog)
        self.workspace = WorkspaceStore(self.settings)
        self.judge = DockerJudge(self.settings, self.catalog, self.dataset, self.workspace)
        self.model = Hy3Client(self.settings.hy3)
        self.image_model = ImageUnderstandingClient(self.settings.image_understanding)
        self.workflow = ContestWorkflow(self.store, self.resources, self.workspace, self.judge, self.model, self.catalog, self.image_model)
        # Separate client/queue: report viewing must never change solving state or cancellation.
        report_options = self.settings.report_translation
        report_model = Hy3Client(
            report_options.model_settings(self.settings.hy3), timeout_seconds=report_options.timeout_seconds,
        )
        self.report_translations = ReportTranslationService(self.settings.runs_root, self.store, report_model, report_options)
        self.background_tasks: set[asyncio.Task] = set()
        self._run_tasks: dict[str, asyncio.Task] = {}
        self._grant_configured_roots()

    def _grant_configured_roots(self) -> None:
        existing = {item["display_name"] for item in self.store.list_scopes()}
        for root in self.settings.resources.roots:
            if root.exists() and root.name not in existing:
                try:
                    self.resources.grant_host_path(str(root), True, configured_root=True)
                except Exception:
                    continue

    def start_run(self, run_id: str) -> None:
        if run_id in self._run_tasks and not self._run_tasks[run_id].done():
            return
        task = asyncio.create_task(self.workflow.execute(run_id), name=f"contestlens:{run_id}")
        self._run_tasks[run_id] = task
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        task.add_done_callback(lambda finished: self._run_tasks.pop(run_id, None) if self._run_tasks.get(run_id) is finished else None)
