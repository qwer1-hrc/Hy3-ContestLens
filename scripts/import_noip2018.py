from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from hy3_contestlens.datasets import ManifestCatalog, import_noip2018, validate_import
from hy3_contestlens.settings import AppSettings


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy NOIP2018 tests into the private judge area without modifying the source")
    parser.add_argument("--source", type=Path, default=PROJECT.parent / "NOI-NOIP_data" / "2018")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    settings = AppSettings.load(PROJECT)
    catalog = ManifestCatalog(settings.manifests_root, settings.default_memory_mb)
    result = validate_import(settings.private_data_root, catalog, "noip2018") if args.verify_only else import_noip2018(args.source, settings.private_data_root, catalog)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
