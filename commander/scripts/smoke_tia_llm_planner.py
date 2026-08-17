"""小模型算法规划 smoke：打印 plan 与 packet 溯源。

用法::

    $env:TIA_ALGORITHM_PLANNER="llm"
    $env:ENABLE_LLM="true"
    $env:TOOL_LLM_URL="http://127.0.0.1:8000/v1"
    $env:TOOL_LLM_NAME="qwen2.5-7b-instruct"
    $env:TIA_ALLOW_LOCAL_FILE="1"
    $env:TIA_EXECUTION_MODE="in_process"
    $env:TIA_COMPUTE_PROFILE="medium"
    .\\.venv\\Scripts\\python.exe scripts/smoke_tia_llm_planner.py

无真 LLM 时可用 Fake（默认）验证管线接线::

    $env:TIA_LLM_SMOKE_FAKE="1"
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TIA_ALLOW_LOCAL_FILE", "1")
os.environ.setdefault("TIA_SKIP_WARMUP", "1")
os.environ.setdefault("TIA_EXECUTION_MODE", "in_process")
os.environ.setdefault("TIA_COMPUTE_PROFILE", "medium")
os.environ.setdefault("TIA_ALGORITHM_PLANNER", "llm")

from agent.algorithm_library.catalog import TIA_DEFAULT_PIPELINE  # noqa: E402
from agent.orchestrator import TacticalIntelligenceAgent  # noqa: E402
from agent.pipeline import agent_config_from_yaml, load_config  # noqa: E402
from tactical_intelligence_agent.payload_adapter import commander_payload_to_batch  # noqa: E402
from workflow_payloads import build_attachment_ref  # noqa: E402
import hashlib  # noqa: E402

EXPORT = ROOT / "examples" / "ue_naval_scenario" / "export" / "OP-IRON-SEA-001"
OUT = ROOT / "data" / "output" / "smoke_tia_llm_planner"


class FakeLLM:
    def chat_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        # 跳过 damage / rag / scheduler / router，保留核心检测跟踪 + 语义压缩
        calls = [
            {
                "algorithm_id": aid,
                "version": "1.0.0",
                "backend_type": "python_http_service",
                "inputs": {"should_be_ignored": True},
                "params": {},
                "reason": "fake plan",
            }
            for aid in (
                "battlefield_rtdetr_detector",
                "edl_evidential_verifier",
                "motr_neural_kalman_tracker",
                "imagebind_multimodal_encoder",
                "supcon_meta_classifier",
                "knowledge_semantic_comm",
            )
        ]
        return {
            "intent": "fake_eo_min_pipeline",
            "algorithm_calls": calls,
            "missing_fields": [],
            "explanation": "FakeLLM：最小感知+分类+语义压缩",
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _local_uri(path: Path) -> str:
    return "local:///" + str(path.resolve()).replace("\\", "/")


def main() -> int:
    image = EXPORT / "battlefield" / "scene_000.png"
    if not image.is_file():
        print(f"[FAIL] missing {image}")
        return 1

    use_fake = os.environ.get("TIA_LLM_SMOKE_FAKE", "1") == "1"
    if not use_fake:
        os.environ.setdefault("ENABLE_LLM", "true")

    cfg = load_config()
    # Fake 模式默认 mock 推理，只验证规划接线；真 LLM/真权重时关闭
    cfg["use_mock"] = use_fake or os.environ.get("TIA_USE_MOCK", "0") == "1"
    cfg.setdefault("algorithm_planner", {})["mode"] = "llm"
    cfg.setdefault("algorithm_planner", {})["fallback_to_fixed"] = True
    if use_fake:
        cfg.setdefault("tool_llm", {})["enable"] = True

    engine = TacticalIntelligenceAgent(
        use_mock=bool(cfg.get("use_mock")),
        config=agent_config_from_yaml(cfg),
    )
    if use_fake:
        engine._llm_client = FakeLLM()
        print("[smoke] using FakeLLM (set TIA_LLM_SMOKE_FAKE=0 for real TOOL_LLM)")

    payload = {
        "schema_version": "1.0",
        "workflow_id": "wf-smoke-llm-planner",
        "work_item": "wf-smoke-llm-planner:frame-000",
        "command": "process_intelligence",
        "required_skill": "tactical_intelligence_analysis",
        "output_hint": "intelligence_packet",
        "attachments": [
            build_attachment_ref(
                _local_uri(image),
                sha256=_sha256(image),
                kind="image",
                attachment_id="att-llm-000",
                meta={
                    "sensor_id": "EO-UAV-01",
                    "modality": "eo_ir",
                    "platform_lat": 30.521,
                    "platform_lon": 114.392,
                    "altitude_m": 3200.0,
                    "heading_deg": 45.0,
                },
            )
        ],
        "input": {"agent_request": {"recon_report": "LLM planner smoke", "sector": "Sector_A"}},
        "context": {
            "jamming_level": 0.0,
            "subscriber_agents": ["commander", "trajectory_predictor"],
            "sea_surface_elevation_m": 0.0,
            "sensor_telemetry": {
                "platform_lat": 30.521,
                "platform_lon": 114.392,
                "altitude_m": 3200.0,
            },
        },
    }

    batch = commander_payload_to_batch(payload)
    packet = engine.process(batch)
    dump = packet.model_dump(mode="json")
    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / "packet.json"
    out_path.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")

    llm_plan = (packet.provenance or {}).get("llm_plan") or {}
    selected = (packet.provenance or {}).get("selected_algorithms") or []
    print(f"[plan] mode={llm_plan.get('mode')} intent={llm_plan.get('intent')}")
    print(f"[plan] selected={selected}")
    print(f"[plan] explanation={llm_plan.get('explanation')}")
    if llm_plan.get("fallback_reason"):
        print(f"[plan] fallback={llm_plan.get('fallback_reason')}")
    skipped = [aid for aid in TIA_DEFAULT_PIPELINE if aid not in set(selected)]
    print(f"[plan] skipped={skipped}")
    print(f"[packet] tracks={len(packet.tracks)} targets={len(packet.targets)} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
