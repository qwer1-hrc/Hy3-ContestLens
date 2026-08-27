from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from hy3_contestlens.datasets import ManifestCatalog, validate_import
from hy3_contestlens.settings import AppSettings


settings = AppSettings.load(PROJECT)
result = validate_import(settings.private_data_root, ManifestCatalog(settings.manifests_root, settings.default_memory_mb))
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result["valid"] else 1)

