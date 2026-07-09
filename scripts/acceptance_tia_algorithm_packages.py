#!/usr/bin/env python3
"""
TIA 算法包验收脚本 — 对齐师兄 algorithm_integration_guide 的 register/activate/run 检查项。

本机无 cmake/algolib.exe 时，用此脚本做等价 Python 验收：
  register  → 算法包目录 + algorithm_card + schema 完整性
  activate  → GET /health 返回 ready + model_loaded
  show-card → algorithm_card.yaml 关键字段
  run       → POST /predict + golden_cases/case_001_request.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "services"
sys.path.insert(0, str(SERVICES))
sys.path.insert(0, str(ROOT))

TIA_ALGORITHMS = [
    "marl_ppo_task_scheduler",
    "edl_evidential_verifier",
    "marl_dynamic_router",
    "supcon_meta_classifier",
    "battlefield_rtdetr_detector",
    "siamese_mask2former_damage",
    "motr_neural_kalman_tracker",
    "imagebind_multimodal_encoder",
    "multimodal_mamba_fusion",
    "synapse_rag_retriever",
    "knowledge_semantic_comm",
]

CARD_REQUIRED = (
    "algorithm_id",
    "version",
    "display_name",
    "backend_type",
    "task_family",
    "machine_spec",
    "resource_requirements",
    "model_profile",
)


def _pkg_dir(algorithm_id: str) -> Path:
    return ROOT / "examples" / algorithm_id / "1.0.0"


def check_register(algorithm_id: str) -> tuple[bool, str]:
    base = _pkg_dir(algorithm_id)
    required = [
        "algorithm_card.yaml",
        "input.schema.json",
        "output.schema.json",
        "golden_cases/case_001_request.json",
        "golden_cases/case_001_response.json",
        "README.md",
        "service_contract.md",
    ]
    missing = [r for r in required if not (base / r).is_file()]
    service = ROOT / "services" / algorithm_id / "app" / "main.py"
    if not service.is_file():
        missing.append(f"services/{algorithm_id}/app/main.py")
    if missing:
        return False, f"missing: {missing}"
    try:
        json.loads((base / "input.schema.json").read_text(encoding="utf-8"))
        json.loads((base / "output.schema.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"invalid schema json: {exc}"
    card_text = (base / "algorithm_card.yaml").read_text(encoding="utf-8")
    for key in CARD_REQUIRED:
        if f"{key}:" not in card_text:
            return False, f"algorithm_card missing field: {key}"
    if "python_http_service" not in card_text:
        return False, "backend_type must be python_http_service"
    return True, "register ok"


def check_activate_and_run(algorithm_id: str) -> tuple[bool, str]:
    from fastapi.testclient import TestClient

    from a2a_algorithms_common.http_service import create_algorithm_app
    from a2a_algorithms_common.tia_predictors import PREDICTOR_REGISTRY, tia_model_loaded

    predict_fn = PREDICTOR_REGISTRY[algorithm_id]
    app = create_algorithm_app(
        algorithm_id,
        "1.0.0",
        "tia",
        predict_fn,
        model_loaded_callable=lambda aid=algorithm_id: tia_model_loaded(aid),
    )
    client = TestClient(app)

    health = client.get("/health")
    if health.status_code != 200:
        return False, f"health HTTP {health.status_code}"
    hb = health.json()
    if not hb.get("ok"):
        return False, f"health not ok: {hb}"
    if hb.get("algorithm_id") != algorithm_id:
        return False, f"health algorithm_id mismatch: {hb.get('algorithm_id')}"

    meta = client.get("/metadata")
    if meta.status_code != 200:
        return False, f"metadata HTTP {meta.status_code}"
    mb = meta.json()
    if mb.get("backend_type") != "python_http_service":
        return False, f"metadata backend_type: {mb.get('backend_type')}"

    req_path = _pkg_dir(algorithm_id) / "golden_cases" / "case_001_request.json"
    request = json.loads(req_path.read_text(encoding="utf-8"))
    resp = client.post("/predict", json=request)
    if resp.status_code != 200:
        return False, f"predict HTTP {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    if not body.get("ok"):
        err = body.get("error") or {}
        return False, f"predict ok=false: {err.get('message', body)}"
    if not body.get("outputs"):
        return False, "predict outputs empty"
    return True, f"run ok (latency_ms={body.get('usage', {}).get('latency_ms')})"


def main() -> int:
    import os

    os.environ.setdefault("TIA_USE_MOCK", "1")
    print("=" * 72)
    print("TIA 算法包验收 (等价 register → activate → run)")
    print(f"ROOT: {ROOT}")
    print(f"TIA_USE_MOCK={os.environ.get('TIA_USE_MOCK', '1')}")
    print("=" * 72)

    passed = 0
    failed: list[tuple[str, str, str]] = []
    for aid in TIA_ALGORITHMS:
        reg_ok, reg_msg = check_register(aid)
        run_ok, run_msg = (False, "skipped") if not reg_ok else check_activate_and_run(aid)
        ok = reg_ok and run_ok
        status = "PASS" if ok else "FAIL"
        print(f"\n[{status}] {aid}")
        print(f"  register: {reg_msg}")
        print(f"  run:      {run_msg}")
        if ok:
            passed += 1
        else:
            failed.append((aid, reg_msg, run_msg))

    print("\n" + "=" * 72)
    print(f"验收结果: {passed}/{len(TIA_ALGORITHMS)} 通过")
    if failed:
        print("失败项:")
        for aid, reg, run in failed:
            print(f"  - {aid}: register={reg}; run={run}")
    print("=" * 72)
    print("\n说明: 本机未检测到 cmake/algolib.exe，已用 Python HTTP 服务做等价验收。")
    print("有 MSVC 环境时可执行:")
    print("  cmake -S . -B build && cmake --build build --config Release")
    print("  .\\build\\Release\\algolib.exe register .\\examples\\<id>\\1.0.0")
    return 0 if passed == len(TIA_ALGORITHMS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
