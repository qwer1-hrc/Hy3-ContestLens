from __future__ import annotations

from pathlib import Path

import pytest

from hy3_contestlens.settings import AppSettings, ResourceSettings


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT.parent / "NOI-NOIP_data" / "2018"


@pytest.fixture()
def settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        project_root=PROJECT,
        database_path=tmp_path / "runs" / "store.sqlite3",
        runs_root=tmp_path / "runs",
        private_data_root=PROJECT / "data" / "private",
        manifests_root=PROJECT / "data" / "manifests",
        resources=ResourceSettings(roots=[SOURCE]),
    )

