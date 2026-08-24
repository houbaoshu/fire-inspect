#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree

VALID_GATES = {
    "browser-e2e",
    "fast",
    "local-acceptance",
    "postgres",
    "real-ai",
    "windows-docx",
}
VALID_FRAMEWORKS = {"playwright", "pytest", "vitest"}
_STATUS_PRIORITY = {"PASSED": 0, "NOT RUN": 1, "FAILED": 2}
_PYTEST_UNICODE_ESCAPE = re.compile(
    r"\\(?P<kind>[uUx])(?P<code>(?:(?<=u)[0-9a-fA-F]{4}|(?<=U)[0-9a-fA-F]{8}|(?<=x)[0-9a-fA-F]{2}))"
)


def _safe_relative_path(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


def _testcase_node_id(*, framework: str, classname: str, name: str, path_prefix: str = "") -> str:
    if framework == "pytest":
        source = classname.replace(".", "/") + ".py"
        # Pytest ASCII-escapes non-ASCII parametrization IDs in its JUnit
        # report even though the canonical node ID accepted by pytest uses the
        # original Unicode text. Normalize only explicit numeric escapes so a
        # literal backslash in an ordinary test name remains untouched.
        name = _PYTEST_UNICODE_ESCAPE.sub(
            lambda match: chr(int(match.group("code"), 16)),
            name,
        )
    else:
        source = classname.replace("\\", "/")
    if path_prefix:
        source = f"{path_prefix.rstrip('/')}/{source.lstrip('/')}"
    return f"{source}::{name}"


def _merge_status(previous: str | None, current: str) -> str:
    if previous is None or _STATUS_PRIORITY[current] > _STATUS_PRIORITY[previous]:
        return current
    return previous


def parse_junit(
    path: Path,
    *,
    framework: str,
    path_prefix: str = "",
) -> tuple[str, dict[str, str], str | None]:
    try:
        root = ElementTree.parse(path).getroot()
    except (ElementTree.ParseError, OSError) as exc:
        return "FAILED", {}, f"invalid JUnit report {path.name}: {type(exc).__name__}"

    tests: dict[str, str] = {}
    for testcase in root.iter("testcase"):
        classname = str(testcase.get("classname", "")).strip()
        name = str(testcase.get("name", "")).strip()
        if not classname or not name:
            continue
        if testcase.find("failure") is not None or testcase.find("error") is not None:
            status = "FAILED"
        elif testcase.find("skipped") is not None:
            status = "NOT RUN"
        else:
            status = "PASSED"
        node_id = _testcase_node_id(
            framework=framework,
            classname=classname,
            name=name,
            path_prefix=path_prefix,
        )
        tests[node_id] = _merge_status(tests.get(node_id), status)

    if not tests:
        return "NOT RUN", {}, None
    suite_status = "PASSED"
    for status in tests.values():
        suite_status = _merge_status(suite_status, status)
    return suite_status, tests, None


def _gate_status(item_statuses: list[str], source_statuses: list[str]) -> str:
    statuses = item_statuses + source_statuses
    if any(status == "FAILED" for status in statuses):
        return "FAILED"
    if statuses and all(status == "PASSED" for status in statuses):
        return "PASSED"
    return "NOT RUN"


def validate_coverage(
    *,
    root: Path,
    coverage_path: Path,
    report_path: Path,
    required_gates: set[str],
) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    try:
        payload = json.loads(coverage_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"cannot read coverage definition: {type(exc).__name__}"]
    if payload.get("schema_version") != 2:
        return {}, ["unsupported coverage schema"]

    result_definitions = payload.get("result_files")
    if not isinstance(result_definitions, dict) or not result_definitions:
        return {}, ["result_files must be a non-empty object"]

    result_reports: dict[str, dict[str, Any]] = {}
    parsed_tests: dict[str, dict[str, str]] = {}
    for result_id, definition in result_definitions.items():
        if not isinstance(result_id, str) or not result_id or not isinstance(definition, dict):
            failures.append("invalid result_files entry")
            continue
        gate = definition.get("gate")
        framework = definition.get("framework")
        relative = _safe_relative_path(definition.get("path"))
        prefix = _safe_relative_path(definition.get("path_prefix", ""))
        if gate not in VALID_GATES:
            failures.append(f"{result_id}: invalid gate")
        if framework not in VALID_FRAMEWORKS:
            failures.append(f"{result_id}: invalid framework")
        if relative is None:
            failures.append(f"{result_id}: invalid result path")
        if definition.get("path_prefix", "") and prefix is None:
            failures.append(f"{result_id}: invalid path prefix")
        if gate not in VALID_GATES or framework not in VALID_FRAMEWORKS or relative is None:
            continue
        result_path = root / relative
        if not result_path.is_file():
            status, tests, error = "NOT RUN", {}, None
        else:
            status, tests, error = parse_junit(
                result_path,
                framework=framework,
                path_prefix=prefix or "",
            )
        if error:
            failures.append(f"{result_id}: {error}")
        parsed_tests[result_id] = tests
        result_reports[result_id] = {
            "gate": gate,
            "path": relative,
            "status": status,
            "test_count": len(tests),
        }

    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return {}, failures + ["items must be a non-empty array"]

    seen_ids: set[str] = set()
    covered_sections: set[str] = set()
    item_reports: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            failures.append("coverage item must be an object")
            continue
        identifier = str(item.get("id", "")).strip()
        if not identifier or identifier in seen_ids:
            failures.append(f"duplicate or missing id: {identifier}")
        seen_ids.add(identifier)
        section = str(item.get("section", "")).strip()
        covered_sections.add(section.split(".", 1)[0])
        gate = item.get("gate")
        if gate not in VALID_GATES:
            failures.append(f"{identifier}: invalid gate")

        artifacts = item.get("artifacts", [])
        if not isinstance(artifacts, list):
            failures.append(f"{identifier}: artifacts must be an array")
            artifacts = []
        for value in artifacts:
            relative = _safe_relative_path(value)
            if relative is None or not (root / relative).exists():
                failures.append(f"{identifier}: missing or unsafe artifact {value}")

        references = item.get("tests")
        if not isinstance(references, list) or not references:
            failures.append(f"{identifier}: no exact test nodes")
            references = []
        reference_reports: list[dict[str, str]] = []
        item_status = "PASSED"
        for reference in references:
            if not isinstance(reference, dict):
                failures.append(f"{identifier}: invalid test reference")
                item_status = "FAILED"
                continue
            result_id = str(reference.get("result", "")).strip()
            node_id = str(reference.get("node", "")).strip()
            definition = result_definitions.get(result_id)
            if not isinstance(definition, dict):
                failures.append(f"{identifier}: unknown result source {result_id}")
                item_status = "FAILED"
                continue
            if definition.get("gate") != gate:
                failures.append(f"{identifier}: test source {result_id} belongs to another gate")
                item_status = "FAILED"
            if "::" not in node_id:
                failures.append(f"{identifier}: test node is not exact: {node_id}")
                item_status = "FAILED"
                continue
            source_path = _safe_relative_path(node_id.split("::", 1)[0])
            if source_path is None or not (root / source_path).is_file():
                failures.append(f"{identifier}: missing test source for {node_id}")
                item_status = "FAILED"
                continue
            source_status = result_reports.get(result_id, {}).get("status", "NOT RUN")
            if source_status == "NOT RUN":
                test_status = "NOT RUN"
            else:
                test_status = parsed_tests.get(result_id, {}).get(node_id, "FAILED")
                if test_status == "FAILED" and node_id not in parsed_tests.get(result_id, {}):
                    failures.append(
                        f"{identifier}: exact test node absent from {result_id}: {node_id}"
                    )
            item_status = _merge_status(item_status, test_status)
            reference_reports.append({"result": result_id, "node": node_id, "status": test_status})
        if not references:
            item_status = "FAILED"
        item_reports.append(
            {
                "id": identifier,
                "section": section,
                "gate": gate,
                "status": item_status,
                "tests": reference_reports,
                "artifacts": artifacts,
            }
        )

    missing_sections = {str(number) for number in range(2, 11)} - covered_sections
    if missing_sections:
        failures.append("uncovered acceptance sections: " + ", ".join(sorted(missing_sections)))

    gate_reports: dict[str, dict[str, Any]] = {}
    for gate in sorted(VALID_GATES):
        item_statuses = [item["status"] for item in item_reports if item["gate"] == gate]
        source_statuses = [
            result["status"] for result in result_reports.values() if result["gate"] == gate
        ]
        gate_reports[gate] = {
            "status": _gate_status(item_statuses, source_statuses),
            "item_count": len(item_statuses),
        }
    for gate in sorted(required_gates):
        if gate not in VALID_GATES:
            failures.append(f"unknown required gate: {gate}")
        elif gate_reports[gate]["status"] != "PASSED":
            failures.append(f"required gate {gate} is {gate_reports[gate]['status']}")

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": str(coverage_path.relative_to(root)),
        "items": item_reports,
        "result_files": result_reports,
        "gates": gate_reports,
        "summary": {
            "items": len(item_reports),
            "passed": sum(item["status"] == "PASSED" for item in item_reports),
            "failed": sum(item["status"] == "FAILED" for item in item_reports),
            "not_run": sum(item["status"] == "NOT RUN" for item in item_reports),
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report, failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-gate", action="append", default=[])
    parser.add_argument("--output", default="test-results/acceptance-coverage.json")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report, failures = validate_coverage(
        root=root,
        coverage_path=root / "acceptance/coverage.json",
        report_path=root / arguments.output,
        required_gates=set(arguments.require_gate),
    )
    if report:
        print(
            json.dumps({"gates": report["gates"], "summary": report["summary"]}, ensure_ascii=False)
        )
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
