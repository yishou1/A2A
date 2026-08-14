#!/usr/bin/env python3
"""Unified acceptance runner for every algorithm package in the repository.

The default mode is intentionally non-invasive: it validates package files,
cards, schemas, golden cases, and duplicate endpoints without starting services.
Use --runtime to call already-running HTTP services and execute native ONNX
models. Use --algolib to add register/activate/run checks with an isolated
registry file.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - environment dependent
    raise SystemExit("PyYAML is required: python -m pip install pyyaml") from exc


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
DEFAULT_REPORT_DIR = ROOT / "build" / "acceptance"
SUPPORTED_BACKENDS = {"onnx", "python_http_service"}
BASE_CARD_FIELDS = (
    "algorithm_id",
    "version",
    "display_name",
    "backend_type",
    "status",
    "task_family",
    "machine_spec",
)
RECOMMENDED_CARD_FIELDS = ("resource_requirements", "model_profile", "safety")


@dataclass
class Check:
    name: str
    status: str
    message: str
    elapsed_ms: float = 0.0


@dataclass
class PackageReport:
    algorithm_id: str
    version: str
    backend_type: str
    package_dir: str
    checks: list[Check] = field(default_factory=list)

    @property
    def result(self) -> str:
        if any(item.status == "FAIL" for item in self.checks):
            return "FAIL"
        return "PASS"

    @property
    def warning_count(self) -> int:
        return sum(item.status == "WARN" for item in self.checks)

    def add(self, name: str, status: str, message: str, elapsed_ms: float = 0.0) -> None:
        self.checks.append(Check(name, status, message, round(elapsed_ms, 3)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Accept all A2A algorithm packages.")
    parser.add_argument(
        "--runtime",
        action="store_true",
        help="Execute ONNX models and call already-running HTTP services.",
    )
    parser.add_argument(
        "--algolib",
        type=Path,
        help="Optional algolib executable used for register/activate/run checks.",
    )
    parser.add_argument(
        "--backend",
        choices=("all", "onnx", "python_http_service"),
        default="all",
        help="Limit acceptance to one backend type.",
    )
    parser.add_argument(
        "--algorithm",
        action="append",
        default=[],
        help="Limit acceptance to an algorithm_id; may be specified more than once.",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP/CLI timeout in seconds.")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help="Directory for acceptance_report.json and acceptance_report.md.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings such as draft status or missing model profile as failures.",
    )
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("YAML root must be an object")
    return value


def nested(card: dict[str, Any], *keys: str) -> Any:
    current: Any = card
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def referenced_path(package: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return package / value


def discover_packages(args: argparse.Namespace) -> list[tuple[Path, dict[str, Any]]]:
    packages: list[tuple[Path, dict[str, Any]]] = []
    requested = set(args.algorithm)
    for card_path in sorted(EXAMPLES.glob("*/[0-9]*.[0-9]*.[0-9]*/algorithm_card.yaml")):
        try:
            card = read_yaml(card_path)
        except Exception as exc:
            card = {"algorithm_id": card_path.parents[1].name, "_load_error": str(exc)}
        backend = str(card.get("backend_type") or "")
        algorithm_id = str(card.get("algorithm_id") or card_path.parents[1].name)
        if args.backend != "all" and backend != args.backend:
            continue
        if requested and algorithm_id not in requested:
            continue
        packages.append((card_path.parent, card))
    return packages


def golden_pairs(package: Path, backend: str) -> list[tuple[Path, Path]]:
    golden_dir = package / "golden_cases"
    if not golden_dir.is_dir():
        return []
    input_suffix, expected_suffix = (
        ("_input.json", "_expected.json")
        if backend == "onnx"
        else ("_request.json", "_response.json")
    )
    pairs: list[tuple[Path, Path]] = []
    for input_path in sorted(golden_dir.glob(f"*{input_suffix}")):
        prefix = input_path.name[: -len(input_suffix)]
        pairs.append((input_path, golden_dir / f"{prefix}{expected_suffix}"))
    return pairs


def validate_static(package: Path, card: dict[str, Any], strict: bool) -> PackageReport:
    algorithm_id = str(card.get("algorithm_id") or package.parents[1].name)
    version = str(card.get("version") or package.name)
    backend = str(card.get("backend_type") or "unknown")
    report = PackageReport(algorithm_id, version, backend, str(package.relative_to(ROOT)))

    if card.get("_load_error"):
        report.add("card", "FAIL", f"algorithm_card.yaml cannot be parsed: {card['_load_error']}")
        return report

    missing_fields = [name for name in BASE_CARD_FIELDS if card.get(name) in (None, "")]
    report.add(
        "card",
        "FAIL" if missing_fields else "PASS",
        f"missing required fields: {missing_fields}" if missing_fields else "required fields present",
    )
    if backend not in SUPPORTED_BACKENDS:
        report.add("backend", "FAIL", f"unsupported backend_type: {backend}")
    else:
        report.add("backend", "PASS", backend)

    recommended_missing = [name for name in RECOMMENDED_CARD_FIELDS if card.get(name) is None]
    if recommended_missing:
        report.add(
            "recommended_fields",
            "FAIL" if strict else "WARN",
            f"missing recommended fields: {recommended_missing}",
        )
    else:
        report.add("recommended_fields", "PASS", "resource, model, and safety fields present")

    if card.get("status") == "draft":
        report.add("lifecycle", "FAIL" if strict else "WARN", "algorithm card status is draft")
    else:
        report.add("lifecycle", "PASS", f"status={card.get('status')}")

    schema_refs = {
        "input_schema": nested(card, "machine_spec", "input_schema_ref"),
        "output_schema": nested(card, "machine_spec", "output_schema_ref"),
    }
    if backend == "onnx":
        schema_refs.update(
            {
                "tensor_contract": nested(card, "machine_spec", "tensor_contract_ref"),
                "model": nested(card, "machine_spec", "runtime", "model_uri"),
                "preprocess": nested(card, "machine_spec", "preprocess", "config_uri"),
                "postprocess": nested(card, "machine_spec", "postprocess", "config_uri"),
            }
        )
    missing_refs: list[str] = []
    invalid_json: list[str] = []
    for label, reference in schema_refs.items():
        path = referenced_path(package, reference)
        if path is None or not path.is_file():
            missing_refs.append(f"{label}={reference!r}")
            continue
        if label.endswith("schema"):
            try:
                read_json(path)
            except Exception as exc:
                invalid_json.append(f"{path.name}: {exc}")
    report.add(
        "references",
        "FAIL" if missing_refs or invalid_json else "PASS",
        "; ".join(missing_refs + invalid_json) if missing_refs or invalid_json else "all referenced files exist",
    )

    pairs = golden_pairs(package, backend)
    golden_errors: list[str] = []
    if not pairs:
        golden_errors.append("no golden case pairs found")
    for input_path, expected_path in pairs:
        if not expected_path.is_file():
            golden_errors.append(f"missing {expected_path.name}")
            continue
        for path in (input_path, expected_path):
            try:
                read_json(path)
            except Exception as exc:
                golden_errors.append(f"{path.name}: {exc}")
    report.add(
        "golden_cases",
        "FAIL" if golden_errors else "PASS",
        "; ".join(golden_errors) if golden_errors else f"{len(pairs)} pair(s) valid JSON",
    )
    return report


def resolve_json_path(value: Any, json_path: str) -> Any:
    if json_path in ("", "$"):
        return value
    if not json_path.startswith("$."):
        raise ValueError(f"unsupported json_path: {json_path}")
    current = value
    for field_name in json_path[2:].split("."):
        current = current[field_name]
    return current


def fnv_token(token: str) -> int:
    value = 1469598103934665603
    for byte in token.encode("utf-8"):
        value ^= byte
        value = (value * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return 100 + value % 1900


def compare_json(expected: Any, actual: Any, path: str = "$") -> list[str]:
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return [] if math.isclose(float(expected), float(actual), abs_tol=1e-6) else [f"{path}: {expected} != {actual}"]
    if type(expected) is not type(actual):
        return [f"{path}: type {type(expected).__name__} != {type(actual).__name__}"]
    if isinstance(expected, dict):
        errors: list[str] = []
        if set(expected) != set(actual):
            errors.append(f"{path}: keys {sorted(expected)} != {sorted(actual)}")
        for key in expected.keys() & actual.keys():
            errors.extend(compare_json(expected[key], actual[key], f"{path}.{key}"))
        return errors
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return [f"{path}: length {len(expected)} != {len(actual)}"]
        errors = []
        for index, (left, right) in enumerate(zip(expected, actual)):
            errors.extend(compare_json(left, right, f"{path}[{index}]"))
        return errors
    return [] if expected == actual else [f"{path}: {expected!r} != {actual!r}"]


def run_onnx(package: Path, card: dict[str, Any]) -> tuple[str, str, float]:
    start = time.perf_counter()
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError as exc:
        return "FAIL", f"ONNX runtime dependency missing: {exc}", 0.0

    pairs = golden_pairs(package, "onnx")
    if not pairs:
        return "FAIL", "no ONNX golden cases", 0.0
    try:
        preprocess = read_yaml(package / nested(card, "machine_spec", "preprocess", "config_uri"))
        postprocess = read_yaml(package / nested(card, "machine_spec", "postprocess", "config_uri"))
        model_path = package / nested(card, "machine_spec", "runtime", "model_uri")
        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        for input_path, expected_path in pairs:
            request = read_json(input_path)
            expected = read_json(expected_path)
            feeds: dict[str, Any] = {}
            preprocess_type = preprocess.get("type")
            if preprocess_type == "json_to_tensor_map":
                for mapping in preprocess.get("mappings") or []:
                    dtype = np.int64 if mapping.get("dtype") == "int64" else np.float32
                    feeds[mapping["tensor_name"]] = np.asarray(
                        resolve_json_path(request, mapping.get("json_path", "$")), dtype=dtype
                    )
            elif preprocess_type == "tensor_from_json":
                model_input = session.get_inputs()[0]
                source = request.get("tensor", request) if isinstance(request, dict) else request
                dtype = np.int64 if "int64" in model_input.type else np.float32
                feeds[model_input.name] = np.asarray(source, dtype=dtype)
            elif preprocess_type == "text_tokenization":
                text = str(request[preprocess.get("input_field", "text")])
                if preprocess.get("lowercase"):
                    text = text.lower()
                tokens = text.split() or ["[EMPTY]"]
                ids = np.asarray([[101, *[fnv_token(token) for token in tokens], 102]], dtype=np.int64)
                feeds = {"input_ids": ids, "attention_mask": np.ones_like(ids)}
            else:
                raise ValueError(f"unsupported preprocess type: {preprocess_type}")

            raw_outputs = session.run(None, feeds)
            output_by_name = {
                spec.name: value.tolist() for spec, value in zip(session.get_outputs(), raw_outputs)
            }
            postprocess_type = postprocess.get("type")
            if postprocess_type == "raw_tensor_to_json":
                actual: Any = {}
                for mapping in postprocess.get("outputs") or []:
                    path = mapping.get("json_path", "$")
                    if not path.startswith("$.") or "." in path[2:]:
                        raise ValueError(f"runtime checker only supports top-level output paths: {path}")
                    actual[path[2:]] = output_by_name[mapping["tensor_name"]]
            elif postprocess_type == "no_op":
                tensor_name = postprocess.get("tensor_name") or session.get_outputs()[0].name
                actual = output_by_name[tensor_name]
            elif postprocess_type == "classification_postprocess":
                tensor_name = postprocess.get("tensor_name", "logits")
                scores = np.asarray(output_by_name[tensor_name], dtype=np.float64).reshape(-1)
                probabilities = np.exp(scores - scores.max())
                probabilities /= probabilities.sum()
                index = int(probabilities.argmax())
                label_map = read_json(package / nested(card, "machine_spec", "postprocess", "label_map_uri"))
                actual = {"label": label_map[str(index)], "confidence": float(probabilities[index])}
            else:
                raise ValueError(f"unsupported postprocess type: {postprocess_type}")
            errors = compare_json(expected, actual)
            if errors:
                return "FAIL", f"{input_path.name}: {errors[0]}", (time.perf_counter() - start) * 1000
    except Exception as exc:
        return "FAIL", f"{type(exc).__name__}: {exc}", (time.perf_counter() - start) * 1000
    return "PASS", f"{len(pairs)} golden case(s) matched", (time.perf_counter() - start) * 1000


def http_json(url: str, timeout: float, payload: Any | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if data is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def run_http(package: Path, card: dict[str, Any], timeout: float) -> tuple[str, str, float]:
    start = time.perf_counter()
    algorithm_id = str(card.get("algorithm_id"))
    runtime = nested(card, "machine_spec", "runtime") or {}
    try:
        health = http_json(runtime["health_endpoint"], timeout)
        if not health.get("ok"):
            raise ValueError(f"health ok=false: {health}")
        metadata = http_json(runtime["metadata_endpoint"], timeout)
        if metadata.get("algorithm_id") not in (None, algorithm_id):
            raise ValueError(f"metadata algorithm_id={metadata.get('algorithm_id')}")
        pairs = golden_pairs(package, "python_http_service")
        for request_path, expected_path in pairs:
            body = http_json(runtime["endpoint"], timeout, read_json(request_path))
            if not body.get("ok"):
                raise ValueError(f"predict ok=false: {body.get('error', body)}")
            outputs = body.get("outputs")
            if outputs in (None, {}, []):
                raise ValueError("predict outputs are empty")
            expected = read_json(expected_path)
            errors = compare_json(expected, outputs)
            if errors:
                raise ValueError(f"{request_path.name}: {errors[0]}")
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return "FAIL", f"{type(exc).__name__}: {exc}", (time.perf_counter() - start) * 1000
    return "PASS", "health, metadata, and golden predict passed", (time.perf_counter() - start) * 1000


def run_algolib(
    package: Path,
    card: dict[str, Any],
    executable: Path,
    registry_path: Path,
    timeout: float,
) -> tuple[str, str, float]:
    start = time.perf_counter()
    algorithm_id = str(card["algorithm_id"])
    version = str(card["version"])
    backend = str(card["backend_type"])
    pairs = golden_pairs(package, backend)
    env = os.environ.copy()
    env["ALGOLIB_REGISTRY_PATH"] = str(registry_path)
    commands = [
        [str(executable), "register", str(package)],
        [str(executable), "activate", algorithm_id, version, backend],
    ]
    if pairs:
        commands.append([str(executable), "run", str(pairs[0][0])])
    for command in commands:
        try:
            process = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "FAIL", f"{command[1]}: {exc}", (time.perf_counter() - start) * 1000
        if process.returncode != 0:
            detail = (process.stdout or process.stderr).strip().replace("\n", " ")
            return "FAIL", f"{command[1]} failed: {detail[:500]}", (time.perf_counter() - start) * 1000
    return "PASS", "register, activate, and run passed", (time.perf_counter() - start) * 1000


def endpoint_warnings(packages: list[tuple[Path, dict[str, Any]]]) -> dict[str, list[str]]:
    endpoints: dict[str, list[str]] = {}
    for _, card in packages:
        if card.get("backend_type") != "python_http_service":
            continue
        endpoint = nested(card, "machine_spec", "runtime", "endpoint")
        if endpoint:
            base = str(endpoint).split("/predict", 1)[0]
            endpoints.setdefault(base, []).append(str(card.get("algorithm_id")))
    return {key: value for key, value in endpoints.items() if len(value) > 1 and "/" not in key[8:]}


def write_reports(
    report_dir: Path,
    package_reports: list[PackageReport],
    duplicate_endpoints: dict[str, list[str]],
    args: argparse.Namespace,
) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "acceptance_report.json"
    markdown_path = report_dir / "acceptance_report.md"
    summary = {
        "pass": sum(report.result == "PASS" for report in package_reports),
        "fail": sum(report.result == "FAIL" for report in package_reports),
        "warnings": sum(report.warning_count for report in package_reports),
        "packages_with_warnings": sum(report.warning_count > 0 for report in package_reports),
    }
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "root": str(ROOT),
        "options": {
            "runtime": args.runtime,
            "algolib": str(args.algolib) if args.algolib else None,
            "backend": args.backend,
            "algorithms": args.algorithm,
            "strict": args.strict,
        },
        "summary": summary,
        "duplicate_endpoints": duplicate_endpoints,
        "packages": [
            asdict(item) | {"result": item.result, "warning_count": item.warning_count}
            for item in package_reports
        ],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Algorithm Acceptance Report",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        f"Result: **PASS {summary['pass']} / FAIL {summary['fail']} / WARNINGS {summary['warnings']}**",
        "",
        "| Algorithm | Backend | Result | Warnings | Static | Runtime | Algolib |",
        "|---|---|---:|---:|---|---|---|",
    ]
    for report in package_reports:
        by_name = {check.name: check for check in report.checks}
        static_checks = [check for check in report.checks if check.name not in ("runtime", "algolib")]
        static_result = "FAIL" if any(check.status == "FAIL" for check in static_checks) else "PASS"
        runtime_result = by_name.get("runtime", Check("runtime", "SKIP", "not requested")).status
        algolib_result = by_name.get("algolib", Check("algolib", "SKIP", "not requested")).status
        lines.append(
            f"| `{report.algorithm_id}` | `{report.backend_type}` | {report.result} | "
            f"{report.warning_count} | "
            f"{static_result} | {runtime_result} | {algolib_result} |"
        )
    if duplicate_endpoints:
        lines.extend(["", "## Duplicate Endpoints", ""])
        for endpoint, algorithms in sorted(duplicate_endpoints.items()):
            lines.append(f"- `{endpoint}`: {', '.join(f'`{item}`' for item in algorithms)}")
    lines.extend(["", "## Details", ""])
    for report in package_reports:
        lines.append(f"### {report.algorithm_id}")
        lines.append("")
        for check in report.checks:
            lines.append(f"- **{check.status}** `{check.name}`: {check.message}")
        lines.append("")
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    args = parse_args()
    packages = discover_packages(args)
    if not packages:
        print("No matching algorithm packages found.", file=sys.stderr)
        return 2

    algolib = args.algolib.resolve() if args.algolib else None
    if algolib and not algolib.is_file():
        print(f"algolib executable not found: {algolib}", file=sys.stderr)
        return 2
    report_dir = args.report_dir.resolve()
    registry_path = report_dir / "registry.json"
    if registry_path.exists():
        registry_path.unlink()

    reports: list[PackageReport] = []
    print(f"Discovered {len(packages)} algorithm package(s).")
    for package, card in packages:
        report = validate_static(package, card, args.strict)
        if args.runtime and not any(check.status == "FAIL" for check in report.checks):
            if report.backend_type == "onnx":
                status, message, elapsed = run_onnx(package, card)
            else:
                status, message, elapsed = run_http(package, card, args.timeout)
            report.add("runtime", status, message, elapsed)
        elif not args.runtime:
            report.add("runtime", "SKIP", "runtime checks were not requested")

        if algolib and not any(check.status == "FAIL" for check in report.checks):
            status, message, elapsed = run_algolib(
                package, card, algolib, registry_path, args.timeout
            )
            report.add("algolib", status, message, elapsed)
        elif not algolib:
            report.add("algolib", "SKIP", "algolib checks were not requested")
        reports.append(report)
        warning_suffix = f" warnings={report.warning_count}" if report.warning_count else ""
        print(
            f"[{report.result}] {report.algorithm_id} "
            f"({report.backend_type}){warning_suffix}"
        )

    duplicates = endpoint_warnings(packages)
    json_path, markdown_path = write_reports(report_dir, reports, duplicates, args)
    pass_count = sum(report.result == "PASS" for report in reports)
    fail_count = sum(report.result == "FAIL" for report in reports)
    warning_count = sum(report.warning_count for report in reports)
    packages_with_warnings = sum(report.warning_count > 0 for report in reports)
    print(
        f"Summary: PASS={pass_count} FAIL={fail_count} "
        f"WARNINGS={warning_count} (packages={packages_with_warnings})"
    )
    if duplicates:
        print(f"Duplicate service endpoints: {len(duplicates)} (see report)")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
