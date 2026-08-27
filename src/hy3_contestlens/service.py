from __future__ import annotations

import asyncio

from .datasets import ManifestCatalog, PrivateDataset
from .judge import DockerJudge
from .model import Hy3Client
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
        self.workflow = ContestWorkflow(self.store, self.resources, self.workspace, self.judge, self.model, self.catalog)
        self.background_tasks: set[asyncio.Task] = set()
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
        task = asyncio.create_task(self.workflow.execute(run_id), name=f"contestlens:{run_id}")
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

