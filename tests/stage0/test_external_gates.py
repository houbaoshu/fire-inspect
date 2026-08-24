from __future__ import annotations

import sys
from pathlib import Path

from scripts.check_external_gate import main


def test_browser_gate_requires_explicit_https_credentials_and_executable(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(sys, "argv", ["check_external_gate.py", "browser-e2e"])
    for name in (
        "FIRE_INSPECT_BROWSER_APPROVED",
        "FIRE_INSPECT_BROWSER_BASE_URL",
        "FIRE_INSPECT_BROWSER_USERNAME",
        "FIRE_INSPECT_BROWSER_PASSWORD",
        "FIRE_INSPECT_BROWSER_EXECUTABLE",
    ):
        monkeypatch.delenv(name, raising=False)

    assert main() == 3
    output = capsys.readouterr().err
    assert "browser-e2e: NOT RUN" in output
    assert "FIRE_INSPECT_BROWSER_PASSWORD" in output


def test_browser_gate_accepts_an_explicit_local_https_target_without_echoing_secret(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    executable = tmp_path / "chrome"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setattr(sys, "argv", ["check_external_gate.py", "browser-e2e"])
    values = {
        "FIRE_INSPECT_BROWSER_APPROVED": "1",
        "FIRE_INSPECT_BROWSER_BASE_URL": "https://127.0.0.1:8443",
        "FIRE_INSPECT_BROWSER_USERNAME": "browser-inspector",
        "FIRE_INSPECT_BROWSER_PASSWORD": "browser-secret-canary",
        "FIRE_INSPECT_BROWSER_EXECUTABLE": str(executable),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    assert main() == 0
    output = capsys.readouterr().out
    assert "explicitly configured" in output
    assert "browser-secret-canary" not in output
