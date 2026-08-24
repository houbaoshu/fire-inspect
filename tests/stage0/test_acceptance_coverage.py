from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_acceptance_coverage import parse_junit, validate_coverage


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _definition() -> dict:
    return {
        "schema_version": 2,
        "result_files": {
            "backend": {
                "gate": "fast",
                "framework": "pytest",
                "path": "test-results/backend.xml",
            },
            "frontend": {
                "gate": "fast",
                "framework": "vitest",
                "path": "test-results/frontend.xml",
                "path_prefix": "frontend",
            },
            "external": {
                "gate": "real-ai",
                "framework": "pytest",
                "path": "test-results/external.xml",
            },
        },
        "items": [
            {
                "id": f"AC-{section}",
                "section": str(section),
                "gate": "fast",
                "tests": [
                    {
                        "result": "backend",
                        "node": "tests/test_backend.py::test_backend",
                    }
                ],
            }
            for section in range(2, 11)
        ]
        + [
            {
                "id": "AC-REAL-AI",
                "section": "2",
                "gate": "real-ai",
                "tests": [
                    {
                        "result": "external",
                        "node": "tests/test_external.py::test_external",
                    }
                ],
            },
            {
                "id": "AC-FRONTEND",
                "section": "10",
                "gate": "fast",
                "tests": [
                    {
                        "result": "frontend",
                        "node": "frontend/src/App.test.tsx::App > renders",
                    }
                ],
            },
        ],
    }


def _prepare_root(tmp_path: Path) -> tuple[Path, Path, Path]:
    for relative in (
        "tests/test_backend.py",
        "tests/test_external.py",
        "frontend/src/App.test.tsx",
    ):
        _write(tmp_path / relative, "# test source\n")
    coverage_path = tmp_path / "acceptance/coverage.json"
    report_path = tmp_path / "test-results/coverage-report.json"
    _write(coverage_path, json.dumps(_definition()))
    return tmp_path, coverage_path, report_path


def test_validator_uses_exact_pytest_and_vitest_nodes_and_reports_external_not_run(
    tmp_path: Path,
) -> None:
    root, coverage_path, report_path = _prepare_root(tmp_path)
    _write(
        root / "test-results/backend.xml",
        '<testsuite><testcase classname="tests.test_backend" name="test_backend" /></testsuite>',
    )
    _write(
        root / "test-results/frontend.xml",
        '<testsuite><testcase classname="src/App.test.tsx" name="App &gt; renders" /></testsuite>',
    )

    report, failures = validate_coverage(
        root=root,
        coverage_path=coverage_path,
        report_path=report_path,
        required_gates={"fast"},
    )

    assert failures == []
    assert report["gates"]["fast"]["status"] == "PASSED"
    assert report["gates"]["real-ai"]["status"] == "NOT RUN"
    assert report["summary"] == {"items": 11, "passed": 10, "failed": 0, "not_run": 1}
    assert json.loads(report_path.read_text(encoding="utf-8"))["items"][-1]["status"] == "PASSED"


def test_validator_fails_required_gate_when_exact_node_is_absent(tmp_path: Path) -> None:
    root, coverage_path, report_path = _prepare_root(tmp_path)
    _write(
        root / "test-results/backend.xml",
        '<testsuite><testcase classname="tests.test_backend" name="a_different_test" /></testsuite>',
    )
    _write(
        root / "test-results/frontend.xml",
        '<testsuite><testcase classname="src/App.test.tsx" name="App &gt; renders" /></testsuite>',
    )

    report, failures = validate_coverage(
        root=root,
        coverage_path=coverage_path,
        report_path=report_path,
        required_gates={"fast"},
    )

    assert report["gates"]["fast"]["status"] == "FAILED"
    assert any("exact test node absent" in failure for failure in failures)
    assert "required gate fast is FAILED" in failures


def test_validator_propagates_junit_failures_instead_of_treating_files_as_evidence(
    tmp_path: Path,
) -> None:
    root, coverage_path, report_path = _prepare_root(tmp_path)
    _write(
        root / "test-results/backend.xml",
        '<testsuite><testcase classname="tests.test_backend" name="test_backend">'
        '<failure message="boom" /></testcase></testsuite>',
    )
    _write(
        root / "test-results/frontend.xml",
        '<testsuite><testcase classname="src/App.test.tsx" name="App &gt; renders" /></testsuite>',
    )

    report, failures = validate_coverage(
        root=root,
        coverage_path=coverage_path,
        report_path=report_path,
        required_gates={"fast"},
    )

    assert report["gates"]["fast"]["status"] == "FAILED"
    assert report["items"][0]["status"] == "FAILED"
    assert "required gate fast is FAILED" in failures


def test_playwright_junit_is_normalized_to_an_exact_repository_node(tmp_path: Path) -> None:
    report_path = tmp_path / "browser.xml"
    _write(
        report_path,
        '<testsuite><testcase classname="compose.e2e.ts" '
        'name="HTTPS smoke"><skipped /></testcase></testsuite>',
    )

    status, tests, error = parse_junit(
        report_path,
        framework="playwright",
        path_prefix="frontend/e2e",
    )

    assert error is None
    assert status == "NOT RUN"
    assert tests == {"frontend/e2e/compose.e2e.ts::HTTPS smoke": "NOT RUN"}


def test_pytest_junit_unicode_parameter_id_is_restored_to_canonical_node(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "backend.xml"
    _write(
        report_path,
        '<testsuite><testcase classname="tests.test_backend" '
        'name="test_kind[\\u5176\\u5b83\\u68c0\\u67e5]" /></testsuite>',
    )

    status, tests, error = parse_junit(report_path, framework="pytest")

    assert error is None
    assert status == "PASSED"
    assert tests == {"tests/test_backend.py::test_kind[其它检查]": "PASSED"}
