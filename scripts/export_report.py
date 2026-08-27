from __future__ import annotations

import argparse
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    response = httpx.get(f"{args.base_url.rstrip('/')}/api/v1/runs/{args.run}/report", timeout=60)
    response.raise_for_status()
    output = args.output or Path("reports") / f"{args.run}.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(response.text, encoding="utf-8")
    print(output.resolve())


if __name__ == "__main__":
    main()

