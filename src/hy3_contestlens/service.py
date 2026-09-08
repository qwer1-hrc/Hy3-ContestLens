from __future__ import annotations

import asyncio
import re
import shutil

from .datasets import ManifestCatalog, PrivateDataset
from .assistant import RunAssistant
from .errors import ContestLensError
from .judge import DockerJudge
from .image_understanding import ImageUnderstandingClient
from .model import Hy3Client
from .report_translation import ReportTranslationService
from .resources import ResourceService
from .settings import AppSettings
from .store import RECOVERABLE_RUN_STATUSES, Store
from .utils import safe_id
from .workflow import ContestWorkflow
from .workspace import WorkspaceStore


class ServiceHub:
    lease_seconds = 15.0
    heartbeat_seconds = 5.0
    recovery_poll_seconds = 5.0

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
        report_model = Hy3Client(report_options.model_settings(self.settings.hy3))
        self.report_translations = ReportTranslationService(self.settings.runs_root, self.store, report_model, report_options)
        self.assistant = RunAssistant(self)
        self._run_tasks: dict[str, asyncio.Task] = {}
        self._supervisor: asyncio.Task | None = None
        self._closing = False
        self.runner_id = safe_id("runner")
        self._grant_configured_roots()

    def _grant_configured_roots(self) -> None:
        existing = {item["display_name"] for item in self.store.list_scopes()}
        for root in self.settings.resources.roots:
            if root.exists() and root.name not in existing:
                try:
                    self.resources.grant_host_path(str(root), True, configured_root=True)
                except Exception:
                    continue

    async def start(self) -> None:
        """Recover durable work before accepting requests, then watch for expired leases."""
        self._closing = False
        self.recover_runs()
        self._supervisor = asyncio.create_task(self._supervise(), name="contestlens:run-supervisor")

    async def close(self) -> None:
        self._closing = True
        if self._supervisor is not None:
            self._supervisor.cancel()
            await asyncio.gather(self._supervisor, return_exceptions=True)
            self._supervisor = None
        tasks = list(self._run_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.report_translations.close()

    def recover_runs(self) -> None:
        if self._closing:
            return
        for run_id in self.store.list_recoverable_runs():
            self.start_run(run_id, queue_if_needed=False)

    async def _supervise(self) -> None:
        while True:
            await asyncio.sleep(self.recovery_poll_seconds)
            self.recover_runs()

    async def _heartbeat(self, run_id: str) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            if not self.store.renew_run_lease(run_id, self.runner_id, lease_seconds=self.lease_seconds):
                return

    async def _execute_claimed_run(self, run_id: str) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(run_id), name=f"contestlens:heartbeat:{run_id}")
        try:
            await self.workflow.execute(run_id)
        except asyncio.CancelledError:
            current = self.store.get_run(run_id)
            if not current["cancelled"] and current["result"] is None:
                self.store.interrupt_run(run_id, reason="service_shutdown" if self._closing else "worker_cancelled")
            raise
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            self.store.release_run_lease(run_id, self.runner_id)

    def start_run(self, run_id: str, *, queue_if_needed: bool = True) -> bool:
        if run_id in self._run_tasks and not self._run_tasks[run_id].done():
            return False
        run = self.store.get_run(run_id)
        if queue_if_needed and run["status"] in {"CREATED", "FAILED", "INTERRUPTED"}:
            run = self.store.queue_run(run_id)
        if run["status"] not in RECOVERABLE_RUN_STATUSES:
            return False
        if not self.store.claim_run(run_id, self.runner_id, lease_seconds=self.lease_seconds):
            return False
        task = asyncio.create_task(self._execute_claimed_run(run_id), name=f"contestlens:{run_id}")
        self._run_tasks[run_id] = task
        task.add_done_callback(lambda finished: self._run_tasks.pop(run_id, None) if self._run_tasks.get(run_id) is finished else None)
        return True

    async def delete_run(self, run_id: str) -> dict:
        run = self.store.get_run(run_id)
        if run["status"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise ContestLensError("RUN_DELETE_REQUIRES_TERMINAL", "Cancel the run before deleting it", {"status": run["status"]}, 409)
        task = self._run_tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.report_translations.forget_run(run_id)
        if not re.fullmatch(r"run_[A-Za-z0-9_-]+", run_id):
            raise ContestLensError("INVALID_IDENTIFIER", "Invalid run identifier", status_code=400)
        root = self.settings.runs_root.resolve()
        target = root / run_id
        tombstone = None
        if target.exists():
            if target.is_symlink() or target.resolve().parent != root:
                raise ContestLensError("RUN_DELETE_PATH_INVALID", "Run artifacts are outside runs_root", status_code=400)
            tombstone = root / f".deleting-{run_id}-{safe_id('delete')}"
            target.rename(tombstone)
        try:
            result = self.store.delete_run(run_id)
        except Exception:
            if tombstone is not None and tombstone.exists() and not target.exists():
                tombstone.rename(target)
            raise
        if tombstone is not None:
            try:
                shutil.rmtree(tombstone)
            except OSError as exc:
                raise ContestLensError("RUN_ARTIFACT_DELETE_FAILED", "Run record was deleted but artifact cleanup failed", {"type": type(exc).__name__}, 500) from exc
        return result

    async def delete_runs(self, run_ids: list[str]) -> dict:
        unique = list(dict.fromkeys(run_ids))
        runs = [self.store.get_run(run_id) for run_id in unique]
        invalid = [{"run_id": run["run_id"], "status": run["status"]} for run in runs if run["status"] not in {"COMPLETED", "FAILED", "CANCELLED"}]
        if invalid:
            raise ContestLensError("RUN_DELETE_REQUIRES_TERMINAL", "Cancel all selected runs before deleting them", {"runs": invalid}, 409)
        root = self.settings.runs_root.resolve()
        for run_id in unique:
            if not re.fullmatch(r"run_[A-Za-z0-9_-]+", run_id):
                raise ContestLensError("INVALID_IDENTIFIER", "Invalid run identifier", status_code=400)
            target = root / run_id
            if target.exists() and (target.is_symlink() or target.resolve().parent != root):
                raise ContestLensError("RUN_DELETE_PATH_INVALID", "Run artifacts are outside runs_root", status_code=400)
        await self.report_translations.forget_runs(set(unique))
        deleted = []
        for run_id in unique:
            deleted.append((await self.delete_run(run_id))["run_id"])
        return {"run_ids": deleted, "deleted": True}
