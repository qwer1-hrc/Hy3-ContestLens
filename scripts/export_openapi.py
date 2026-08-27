from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
os.environ["HY3_CONTESTLENS_ROOT"] = str(PROJECT)

from hy3_contestlens.api.app import create_app

(PROJECT / "docs" / "openapi.json").write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(PROJECT / "docs" / "openapi.json")

