#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit


def missing_real_ai() -> list[str]:
    missing: list[str] = []
    if os.environ.get("FIRE_INSPECT_REAL_AI_APPROVED") != "1":
        missing.append("FIRE_INSPECT_REAL_AI_APPROVED=1")
    for capability in ("ASR", "LLM", "VISION", "EMBEDDING"):
        prefix = f"FIRE_INSPECT_{capability}"
        if os.environ.get(f"{prefix}_PROVIDER") != "http":
            missing.append(f"{prefix}_PROVIDER=http")
        for suffix in ("ENDPOINT", "MODEL", "API_KEY"):
            name = f"{prefix}_{suffix}"
            if not os.environ.get(name):
                missing.append(name)
    return missing


def main() -> int:
    gate = sys.argv[1] if len(sys.argv) > 1 else ""
    if gate == "real-ai":
        missing = missing_real_ai()
    elif gate == "windows-docx":
        missing = []
        if os.environ.get("FIRE_INSPECT_WINDOWS_DOCX_APPROVED") != "1":
            missing.append("FIRE_INSPECT_WINDOWS_DOCX_APPROVED=1")
        for name in (
            "FIRE_INSPECT_WINDOWS_DOCX_OUTPUT_DIR",
            "FIRE_INSPECT_WINDOWS_DOCX_APPROVAL_FILE",
        ):
            if not os.environ.get(name):
                missing.append(name)
    elif gate == "postgres":
        missing = (
            []
            if os.environ.get("FIRE_INSPECT_POSTGRES_TEST_URL")
            else ["FIRE_INSPECT_POSTGRES_TEST_URL"]
        )
    elif gate == "browser-e2e":
        missing = []
        if os.environ.get("FIRE_INSPECT_BROWSER_APPROVED") != "1":
            missing.append("FIRE_INSPECT_BROWSER_APPROVED=1")
        for name in (
            "FIRE_INSPECT_BROWSER_BASE_URL",
            "FIRE_INSPECT_BROWSER_USERNAME",
            "FIRE_INSPECT_BROWSER_PASSWORD",
            "FIRE_INSPECT_BROWSER_EXECUTABLE",
        ):
            if not os.environ.get(name):
                missing.append(name)
        base_url = os.environ.get("FIRE_INSPECT_BROWSER_BASE_URL", "")
        if base_url and urlsplit(base_url).scheme != "https":
            missing.append("FIRE_INSPECT_BROWSER_BASE_URL=https://...")
        executable = os.environ.get("FIRE_INSPECT_BROWSER_EXECUTABLE", "")
        if executable and not (Path(executable).is_file() and os.access(executable, os.X_OK)):
            missing.append("FIRE_INSPECT_BROWSER_EXECUTABLE=<executable Chrome path>")
    else:
        print("unknown external gate", file=sys.stderr)
        return 2
    if missing:
        print(f"{gate}: NOT RUN; missing explicit configuration:", file=sys.stderr)
        for name in missing:
            print(f"- {name}", file=sys.stderr)
        return 3
    print(f"{gate}: external gate is explicitly configured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
