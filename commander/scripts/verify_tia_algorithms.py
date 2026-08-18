#!/usr/bin/env python3
"""验证 TIA 11 个算法包在 mock / real 模式下的可用性。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "services"
sys.path.insert(0, str(SERVICES))
sys.path.insert(0, str(ROOT))

from a2a_algorithms_common.tia_predictors import (  # noqa: E402
    PREDICTOR_REGISTRY,
    tia_model_loaded,
)
from scripts.accept_all_algorithms import find_non_real_markers  # noqa: E402

CHECKPOINT_DIR = ROOT / "models" / "checkpoints"

ALGORITHM_META = {
    "battlefield_rtdetr_detector": {
        "weights": ["battlefield_rtdetr.pt", "odconv_refiner.pt", "rtdetr-l.pt"],
        "real_deps": ["ultralytics", "torch"],
    },
    "siamese_mask2former_damage": {
        "weights": [],
        "real_deps": ["transformers", "torch"],
    },
    "edl_evidential_verifier": {
        "weights": ["edl_head_s.safetensors", "edl_head.pt"],
        "real_deps": ["torch", "safetensors"],
    },
    "motr_neural_kalman_tracker": {
        "weights": ["motr_tracker.pt", "motr_tracker_battlefield.pt"],
        "real_deps": ["torch"],
    },
    "marl_ppo_task_scheduler": {
        "weights": ["marl_ppo_scheduler_s.safetensors", "marl_ppo_scheduler.pt"],
        "real_deps": ["torch", "numpy", "safetensors"],
    },
    "imagebind_multimodal_encoder": {
        "weights": [],
        "real_deps": ["torch"],
    },
    "multimodal_mamba_fusion": {
        "weights": [
            "mamba_fusion_s.safetensors",
            "mamba_fusion.pt",
            "mamba_fusion_s.pt",
            "mamba_fusion_l.pt",
        ],
        "real_deps": ["torch", "safetensors"],
    },
    "supcon_meta_classifier": {
        "weights": ["supcon_meta_s.safetensors", "supcon_meta.pt", "supcon_meta_l.pt"],
        "real_deps": ["torch"],
    },
    "synapse_rag_retriever": {
        "weights": [],
        "real_deps": ["sentence_transformers"],
    },
    "knowledge_semantic_comm": {
        "weights": [],
        "real_deps": ["transformers", "torch"],
    },
    "marl_dynamic_router": {
        "weights": ["marl_policy.pt"],
        "real_deps": ["torch"],
    },
}


@dataclass
class AlgoReport:
    algorithm_id: str
    package_ok: bool = False
    mock_ok: bool = False
    mock_error: str = ""
    real_ok: bool = False
    real_error: str = ""
    weights_found: list[str] = field(default_factory=list)
    weights_missing: list[str] = field(default_factory=list)
    model_loaded_real: bool = False
    mock_latency_ms: float = 0.0
    real_latency_ms: float = 0.0


def _golden_inputs(algorithm_id: str) -> dict:
    path = ROOT / "examples" / algorithm_id / "1.0.0" / "golden_cases" / "case_001_request.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["inputs"]


def _package_ok(algorithm_id: str) -> bool:
    base = ROOT / "examples" / algorithm_id / "1.0.0"
    required = [
        "algorithm_card.yaml",
        "input.schema.json",
        "output.schema.json",
        "golden_cases/case_001_request.json",
        "golden_cases/case_001_response.json",
        "service_contract.md",
    ]
    service = ROOT / "services" / algorithm_id / "app" / "main.py"
    return all((base / name).is_file() for name in required) and service.is_file()


def _check_weights(algorithm_id: str) -> tuple[list[str], list[str]]:
    meta = ALGORITHM_META.get(algorithm_id, {})
    found: list[str] = []
    missing: list[str] = []
    for name in meta.get("weights", []):
        candidates = [
            CHECKPOINT_DIR / name,
            ROOT / name,
            ROOT / "models" / name,
        ]
        if any(p.is_file() for p in candidates):
            found.append(name)
        else:
            missing.append(name)
    return found, missing


def _run_predict(algorithm_id: str, use_mock: bool) -> tuple[bool, str, float, dict | None]:
    os.environ["TIA_USE_MOCK"] = "1" if use_mock else "0"
    # clear lru caches between modes
    import a2a_algorithms_common.tia_predictors as tp

    tp._config.cache_clear()
    tp._backend.cache_clear()

    predict_fn = PREDICTOR_REGISTRY[algorithm_id]
    inputs = _golden_inputs(algorithm_id)
    start = time.perf_counter()
    try:
        outputs = predict_fn(inputs, {})
        latency = (time.perf_counter() - start) * 1000.0
        if not outputs:
            return False, "empty outputs", latency, None
        return True, "", latency, outputs
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000.0
        return False, f"{type(exc).__name__}: {exc}", latency, None


def _http_predict_ok(algorithm_id: str) -> tuple[bool, str]:
    try:
        from fastapi.testclient import TestClient
        from a2a_algorithms_common.http_service import create_algorithm_app

        predict_fn = PREDICTOR_REGISTRY[algorithm_id]
        app = create_algorithm_app(
            algorithm_id,
            "1.0.0",
            "tia",
            predict_fn,
            model_loaded_callable=lambda aid=algorithm_id: tia_model_loaded(aid),
        )
        client = TestClient(app)
        req_path = ROOT / "examples" / algorithm_id / "1.0.0" / "golden_cases" / "case_001_request.json"
        request = json.loads(req_path.read_text(encoding="utf-8"))
        resp = client.post("/predict", json=request)
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        body = resp.json()
        if not body.get("ok"):
            err = body.get("error") or {}
            return False, str(err.get("message", body))
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def verify_all() -> list[AlgoReport]:
    reports: list[AlgoReport] = []
    for algorithm_id in PREDICTOR_REGISTRY:
        report = AlgoReport(algorithm_id=algorithm_id)
        report.package_ok = _package_ok(algorithm_id)
        report.weights_found, report.weights_missing = _check_weights(algorithm_id)

        ok, err, latency, _ = _run_predict(algorithm_id, use_mock=True)
        report.mock_ok = ok
        report.mock_error = err
        report.mock_latency_ms = round(latency, 2)

        http_ok, http_err = _http_predict_ok(algorithm_id)
        if not http_ok:
            report.mock_ok = False
            report.mock_error = f"http: {http_err}"

        os.environ["TIA_USE_MOCK"] = "0"
        import a2a_algorithms_common.tia_predictors as tp

        tp._config.cache_clear()
        tp._backend.cache_clear()
        report.model_loaded_real = tia_model_loaded(algorithm_id)

        ok, err, latency, outputs = _run_predict(algorithm_id, use_mock=False)
        if ok and not report.model_loaded_real:
            ok = False
            err = "model_loaded=false; required trained checkpoint is unavailable"
        if ok and outputs is not None:
            markers = find_non_real_markers(outputs)
            if markers:
                ok = False
                err = f"non-real runtime marker: {markers[0]}"
        report.real_ok = ok
        report.real_error = err
        report.real_latency_ms = round(latency, 2)

        reports.append(report)
    return reports


def print_report(reports: list[AlgoReport], mode: str = "all") -> int:
    print("=" * 88)
    print("TIA 算法可用性验证报告")
    print(f"项目根目录: {ROOT}")
    print(f"权重目录:   {CHECKPOINT_DIR} (exists={CHECKPOINT_DIR.is_dir()})")
    print("=" * 88)

    mock_pass = real_pass = pkg_pass = 0
    for r in reports:
        pkg_pass += int(r.package_ok)
        mock_pass += int(r.mock_ok)
        real_pass += int(r.real_ok)
        status = []
        status.append("PKG OK" if r.package_ok else "PKG MISSING")
        status.append("MOCK OK" if r.mock_ok else f"MOCK FAIL: {r.mock_error}")
        if r.real_ok:
            status.append(f"REAL OK ({r.real_latency_ms}ms)")
        else:
            w = f", weights missing: {r.weights_missing}" if r.weights_missing else ""
            status.append(f"REAL FAIL: {r.real_error}{w}")
        print(f"\n[{r.algorithm_id}]")
        print("  " + " | ".join(status))
        if r.weights_found:
            print(f"  weights found: {r.weights_found}")
        if r.weights_missing:
            print(f"  weights missing: {r.weights_missing}")
        print(f"  model_loaded(real): {r.model_loaded_real}")

    print("\n" + "=" * 88)
    print(f"算法包完整: {pkg_pass}/{len(reports)}")
    print(f"Mock 可用:  {mock_pass}/{len(reports)}")
    print(f"Real 可用:  {real_pass}/{len(reports)}")
    print("=" * 88)

    if real_pass < len(reports):
        print("\n说明:")
        print("- Mock 模式 (TIA_USE_MOCK=1) 使用启发式逻辑，无需 GPU 权重，用于联调/验收。")
        print("- Real 模式 (TIA_USE_MOCK=0) 需要 models/checkpoints/ 权重及 HuggingFace/Ultralytics 模型。")
        print("- 运行 scripts/download_models.py --heads-only 可初始化辅助头权重。")
        print("- 检测器还需 rtdetr-l.pt 或 battlefield_rtdetr.pt；语义/RAG 需联网下载预训练模型。")

    package_pass = pkg_pass == len(reports)
    mock_complete = mock_pass == len(reports)
    real_complete = real_pass == len(reports)
    if mode == "mock":
        return 0 if package_pass and mock_complete else 1
    if mode == "real":
        return 0 if package_pass and real_complete else 1
    return 0 if package_pass and mock_complete and real_complete else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify TIA package, mock, and real runtimes.")
    parser.add_argument(
        "--mode",
        choices=("mock", "real", "all"),
        default="all",
        help="Select which acceptance result controls the process exit code.",
    )
    args = parser.parse_args()
    reports = verify_all()
    return print_report(reports, args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
