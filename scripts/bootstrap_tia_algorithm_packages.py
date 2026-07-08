#!/usr/bin/env python3
"""Bootstrap TIA 战术情报 Agent 子算法为 algorithmrepo 标准包。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "services"
sys.path.insert(0, str(SERVICES))
sys.path.insert(0, str(ROOT))

from a2a_algorithms_common.tia_predictors import PREDICTOR_REGISTRY, tia_model_loaded

TIA_ALGORITHMS = [
    {
        "algorithm_id": "marl_ppo_task_scheduler",
        "display_name": "MARL-PPO Task Scheduler",
        "port": 9024,
        "task_family": "planning",
        "primary_metric": "schedule_coverage",
        "primary_score": 0.82,
        "risk_level": "medium",
        "timeout_ms": 5000,
        "capabilities": ["sensor_task_scheduling", "reattack_planning", "marl_ppo"],
        "when_to_use": "Tracks and battlefield context are available for sensor coverage and reattack allocation.",
        "when_not_to_use": "No targets or sensors in the operational picture.",
    },
    {
        "algorithm_id": "edl_evidential_verifier",
        "display_name": "EDL Evidential Verifier",
        "port": 9022,
        "task_family": "scoring",
        "primary_metric": "verification_precision",
        "primary_score": 0.88,
        "risk_level": "low",
        "timeout_ms": 3000,
        "capabilities": ["evidential_verification", "uncertainty_estimation"],
        "when_to_use": "Raw detections need epistemic filtering before tracking.",
        "when_not_to_use": "No detection candidates are provided.",
    },
    {
        "algorithm_id": "marl_dynamic_router",
        "display_name": "MARL Dynamic Router",
        "port": 9030,
        "task_family": "decision",
        "primary_metric": "routing_reliability",
        "primary_score": 0.85,
        "risk_level": "medium",
        "timeout_ms": 3000,
        "capabilities": ["anti_jam_routing", "semantic_comm_routing"],
        "when_to_use": "Intelligence packet must be routed under jamming constraints.",
        "when_not_to_use": "No subscriber agents or packet summary is available.",
    },
    {
        "algorithm_id": "supcon_meta_classifier",
        "display_name": "SupCon Meta Classifier",
        "port": 9027,
        "task_family": "classification",
        "primary_metric": "classification_f1",
        "primary_score": 0.80,
        "risk_level": "medium",
        "timeout_ms": 3000,
        "capabilities": ["few_shot_classification", "affiliation_labeling"],
        "when_to_use": "Fused target embeddings need hostile/friendly labels.",
        "when_not_to_use": "No fused embeddings are available.",
    },
    {
        "algorithm_id": "battlefield_rtdetr_detector",
        "display_name": "Battlefield RT-DETR Detector",
        "port": 9020,
        "task_family": "detection",
        "primary_metric": "detection_map",
        "primary_score": 0.75,
        "risk_level": "medium",
        "timeout_ms": 15000,
        "capabilities": ["object_detection", "rt_detr", "odconv"],
        "when_to_use": "EO/IR or SAR visual frames need battlefield object detection.",
        "when_not_to_use": "Only unstructured text without imagery is available.",
    },
    {
        "algorithm_id": "siamese_mask2former_damage",
        "display_name": "Siamese Mask2Former Damage Assessor",
        "port": 9021,
        "task_family": "segmentation",
        "primary_metric": "damage_score_mae",
        "primary_score": 0.78,
        "risk_level": "medium",
        "timeout_ms": 15000,
        "capabilities": ["damage_assessment", "change_detection"],
        "when_to_use": "Pre/post strike imagery is available for BDA.",
        "when_not_to_use": "No visual frames are provided.",
    },
    {
        "algorithm_id": "motr_neural_kalman_tracker",
        "display_name": "MOTR Neural Kalman Tracker",
        "port": 9023,
        "task_family": "tracking",
        "primary_metric": "track_continuity",
        "primary_score": 0.83,
        "risk_level": "medium",
        "timeout_ms": 8000,
        "capabilities": ["multi_object_tracking", "geolocation"],
        "when_to_use": "Verified detections need persistent tracks and geo estimation.",
        "when_not_to_use": "No verified detections are available.",
    },
    {
        "algorithm_id": "imagebind_multimodal_encoder",
        "display_name": "ImageBind Multimodal Encoder",
        "port": 9025,
        "task_family": "embedding",
        "primary_metric": "embedding_quality",
        "primary_score": 0.86,
        "risk_level": "low",
        "timeout_ms": 10000,
        "capabilities": ["multimodal_embedding", "cross_modal_encoding"],
        "when_to_use": "Multiple sensor modalities need unified embeddings.",
        "when_not_to_use": "No sensor frames are provided.",
    },
    {
        "algorithm_id": "multimodal_mamba_fusion",
        "display_name": "Multimodal Mamba Fusion",
        "port": 9026,
        "task_family": "fusion",
        "primary_metric": "fusion_coherence",
        "primary_score": 0.84,
        "risk_level": "low",
        "timeout_ms": 5000,
        "capabilities": ["temporal_fusion", "multimodal_mamba"],
        "when_to_use": "Embeddings and track history need temporal fusion.",
        "when_not_to_use": "No embeddings or tracks are available.",
    },
    {
        "algorithm_id": "synapse_rag_retriever",
        "display_name": "Synapse RAG Retriever",
        "port": 9028,
        "task_family": "retrieval",
        "primary_metric": "rag_relevance",
        "primary_score": 0.81,
        "risk_level": "medium",
        "timeout_ms": 8000,
        "capabilities": ["knowledge_retrieval", "entity_linking"],
        "when_to_use": "Classifications need knowledge-base context enrichment.",
        "when_not_to_use": "No knowledge base or classifications are available.",
    },
    {
        "algorithm_id": "knowledge_semantic_comm",
        "display_name": "Knowledge Semantic Comm",
        "port": 9029,
        "task_family": "generation",
        "primary_metric": "compression_ratio",
        "primary_score": 0.79,
        "risk_level": "medium",
        "timeout_ms": 10000,
        "capabilities": ["semantic_compression", "intelligence_summary"],
        "when_to_use": "Perception and cognition outputs need semantic packet compression.",
        "when_not_to_use": "Neither perception nor cognition payloads are available.",
    },
]

GOLDEN_INPUTS: dict[str, dict] = {
    "marl_ppo_task_scheduler": {
        "tracks": [
            {"track_id": "T-1", "class_name": "tank", "geo": {"lat": 30.52, "lon": 114.38}},
            {"track_id": "T-2", "class_name": "vehicle", "geo": {"lat": 30.51, "lon": 114.39}},
        ],
        "detections": [
            {"track_id": "T-1", "confidence": 0.9, "damage_score": 0.3, "class_name": "tank"},
            {"track_id": "T-2", "confidence": 0.8, "damage_score": 0.7, "class_name": "vehicle"},
        ],
        "batch_context": {"phase": "bda", "jamming_level": 0.2},
        "frames": [
            {"sensor_id": "EO-FWD-1", "modality": "eo_ir", "metadata": {"platform_lat": 30.5, "platform_lon": 114.37}},
            {"sensor_id": "SAR-1", "modality": "sar", "metadata": {"platform_lat": 30.5, "platform_lon": 114.37}},
        ],
    },
    "edl_evidential_verifier": {
        "detections": [
            {"sensor_id": "EO-1", "class_name": "tank", "confidence": 0.91, "bbox": [10, 20, 80, 90]},
            {"sensor_id": "EO-1", "class_name": "person", "confidence": 0.42, "bbox": [120, 40, 140, 90]},
        ]
    },
    "marl_dynamic_router": {
        "packet": {"summary": "2 high-threat targets", "targets": [{"threat_level": "high"}]},
        "subscriber_agents": ["command_agent", "fire_control_agent"],
        "jamming_level": 0.65,
    },
    "supcon_meta_classifier": {
        "fused_embeddings": {"T-1": [0.2, 0.5, 0.8, 0.1], "T-2": [0.1, 0.3, 0.4, 0.9]},
        "support_shots": [],
    },
    "battlefield_rtdetr_detector": {
        "frames": [
            {
                "sensor_id": "EO-FWD-1",
                "modality": "eo_ir",
                "payload": {"scene_tag": "recon_corridor"},
            }
        ]
    },
    "siamese_mask2former_damage": {
        "frames": [{"sensor_id": "EO-FWD-1", "modality": "eo_ir", "payload": {"scene_tag": "post_strike"}}],
        "reference_frame": {"sensor_id": "EO-REF", "modality": "eo_ir", "payload": {"scene_tag": "pre_strike"}},
    },
    "motr_neural_kalman_tracker": {
        "verified_detections": [
            {"track_id": "T-1", "class_name": "tank", "confidence": 0.9, "bbox": [10, 20, 80, 90]}
        ],
        "prior_tracks": [],
        "visual_frame": {"sensor_id": "EO-FWD-1", "modality": "eo_ir"},
        "batch_context": {"base_lat": 30.512, "base_lon": 114.381, "phase": "contact"},
    },
    "imagebind_multimodal_encoder": {
        "frames": [
            {"sensor_id": "EO-FWD-1", "modality": "eo_ir", "payload": {"scene_tag": "contact"}},
            {"sensor_id": "FO-1", "modality": "text_report", "payload": {"report_text": "前沿发现装甲目标"}},
        ]
    },
    "multimodal_mamba_fusion": {
        "embeddings": {"eo_ir": [0.1, 0.2, 0.3], "text_report": [0.4, 0.5, 0.6]},
        "tracks": [{"track_id": "T-1", "class_name": "tank"}],
    },
    "synapse_rag_retriever": {
        "classifications": [{"target_id": "T-1", "label": "hostile", "confidence": 0.88}],
        "knowledge_base": [{"title": "装甲威胁规则", "content": "高威胁坦克需优先分配火力"}],
        "query": "战场目标实体与威胁关联",
    },
    "knowledge_semantic_comm": {
        "perception": {
            "detections": [{"track_id": "T-1", "class_name": "tank", "confidence": 0.9}],
            "tracks": [{"track_id": "T-1"}],
        },
        "cognition": {
            "classifications": [{"target_id": "T-1", "label": "hostile"}],
            "threats": [{"target_id": "T-1", "threat_level": "high"}],
        },
    },
}

INPUT_SCHEMAS: dict[str, dict] = {
    "marl_ppo_task_scheduler": {
        "title": "MARL-PPO Task Scheduler Input",
        "type": "object",
        "required": ["tracks", "detections", "frames"],
        "properties": {
            "tracks": {"type": "array", "items": {"type": "object"}},
            "detections": {"type": "array", "items": {"type": "object"}},
            "batch_context": {"type": "object"},
            "frames": {"type": "array", "items": {"type": "object"}},
        },
        "additionalProperties": True,
    },
    "edl_evidential_verifier": {
        "title": "EDL Evidential Verifier Input",
        "type": "object",
        "required": ["detections"],
        "properties": {"detections": {"type": "array", "items": {"type": "object"}}},
        "additionalProperties": True,
    },
    "marl_dynamic_router": {
        "title": "MARL Dynamic Router Input",
        "type": "object",
        "required": ["packet"],
        "properties": {
            "packet": {"type": "object"},
            "subscriber_agents": {"type": "array", "items": {"type": "string"}},
            "jamming_level": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "additionalProperties": True,
    },
    "supcon_meta_classifier": {
        "title": "SupCon Meta Classifier Input",
        "type": "object",
        "required": ["fused_embeddings"],
        "properties": {
            "fused_embeddings": {"type": "object"},
            "support_shots": {"type": "array", "items": {"type": "object"}},
        },
        "additionalProperties": True,
    },
    "battlefield_rtdetr_detector": {
        "title": "Battlefield RT-DETR Detector Input",
        "type": "object",
        "properties": {
            "frames": {"type": "array", "items": {"type": "object"}},
            "media_refs": {"type": "array", "items": {"type": "object"}},
        },
        "additionalProperties": True,
    },
    "siamese_mask2former_damage": {
        "title": "Siamese Mask2Former Damage Input",
        "type": "object",
        "required": ["frames"],
        "properties": {
            "frames": {"type": "array", "items": {"type": "object"}},
            "reference_frame": {"type": "object"},
        },
        "additionalProperties": True,
    },
    "motr_neural_kalman_tracker": {
        "title": "MOTR Neural Kalman Tracker Input",
        "type": "object",
        "required": ["verified_detections"],
        "properties": {
            "verified_detections": {"type": "array", "items": {"type": "object"}},
            "prior_tracks": {"type": "array", "items": {"type": "object"}},
            "visual_frame": {"type": "object"},
            "batch_context": {"type": "object"},
        },
        "additionalProperties": True,
    },
    "imagebind_multimodal_encoder": {
        "title": "ImageBind Multimodal Encoder Input",
        "type": "object",
        "required": ["frames"],
        "properties": {"frames": {"type": "array", "items": {"type": "object"}}},
        "additionalProperties": True,
    },
    "multimodal_mamba_fusion": {
        "title": "Multimodal Mamba Fusion Input",
        "type": "object",
        "required": ["embeddings"],
        "properties": {
            "embeddings": {"type": "object"},
            "tracks": {"type": "array", "items": {"type": "object"}},
        },
        "additionalProperties": True,
    },
    "synapse_rag_retriever": {
        "title": "Synapse RAG Retriever Input",
        "type": "object",
        "required": ["classifications"],
        "properties": {
            "classifications": {"type": "array", "items": {"type": "object"}},
            "knowledge_base": {"type": "array", "items": {"type": "object"}},
            "query": {"type": "string"},
        },
        "additionalProperties": True,
    },
    "knowledge_semantic_comm": {
        "title": "Knowledge Semantic Comm Input",
        "type": "object",
        "required": ["perception", "cognition"],
        "properties": {
            "perception": {"type": "object"},
            "cognition": {"type": "object"},
        },
        "additionalProperties": True,
    },
}


def _infer_output_schema(outputs: dict) -> dict:
    return {
        "title": "TIA Algorithm Output",
        "type": "object",
        "properties": {key: {"type": "object" if isinstance(val, dict) else "array" if isinstance(val, list) else "number" if isinstance(val, (int, float)) else "string"} for key, val in outputs.items()},
        "additionalProperties": True,
    }


def write_service_main(meta: dict) -> None:
    algorithm_id = meta["algorithm_id"]
    port = meta["port"]
    service_dir = SERVICES / algorithm_id / "app"
    service_dir.mkdir(parents=True, exist_ok=True)
    main_py = f'''#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(ROOT))

from a2a_algorithms_common.http_service import create_algorithm_app
from a2a_algorithms_common.tia_predictors import predict_{algorithm_id}, tia_model_loaded

ALGORITHM_ID = "{algorithm_id}"
VERSION = "1.0.0"
PORT = int(os.environ.get("PORT", "{port}"))


def _predict(inputs: dict, params: dict) -> dict:
    return predict_{algorithm_id}(inputs, params)


app = create_algorithm_app(
    ALGORITHM_ID,
    VERSION,
    "{meta["task_family"]}",
    _predict,
    model_loaded_callable=lambda: tia_model_loaded(ALGORITHM_ID),
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
'''
    (service_dir / "main.py").write_text(main_py, encoding="utf-8")
    readme = f"""# {meta['display_name']} Service

TIA algorithm packaged for the A2A algorithm library.

Run:

```powershell
$env:TIA_USE_MOCK="1"
$env:PORT="{port}"
python services/{algorithm_id}/app/main.py
```
"""
    (SERVICES / algorithm_id / "README.md").write_text(readme, encoding="utf-8")


def write_example_package(meta: dict, outputs: dict) -> None:
    algorithm_id = meta["algorithm_id"]
    port = meta["port"]
    pkg = ROOT / "examples" / algorithm_id / "1.0.0"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "input.schema.json").write_text(
        json.dumps(INPUT_SCHEMAS[algorithm_id], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (pkg / "output.schema.json").write_text(
        json.dumps(_infer_output_schema(outputs), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    caps = meta["capabilities"]
    cap_yaml = "\n".join(f"  - {cap}" for cap in caps)
    card = f"""algorithm_id: {algorithm_id}
version: 1.0.0
display_name: {meta['display_name']}
backend_type: python_http_service
status: draft

task_family: {meta['task_family']}
modalities:
  input:
    - structured_json
  output:
    - structured_json

capabilities:
{cap_yaml}

agent_card:
  summary: >
    {meta['display_name']} from the TIA tactical intelligence agent pipeline.
  when_to_use:
    - {meta['when_to_use']}
  when_not_to_use:
    - {meta['when_not_to_use']}
  input_description: >
    See input.schema.json for required structured fields.
  output_description: >
    See output.schema.json for structured outputs.

machine_spec:
  input_schema_ref: input.schema.json
  output_schema_ref: output.schema.json
  runtime:
    backend_type: python_http_service
    endpoint: http://127.0.0.1:{port}/predict
    health_endpoint: http://127.0.0.1:{port}/health
    metadata_endpoint: http://127.0.0.1:{port}/metadata
    timeout_ms: {meta['timeout_ms']}

constraints:
  max_input_chars: 20000000
  max_request_bytes: 33554432
  batch_supported: false
  streaming_supported: false

performance:
  latency_ms_p50: 50
  latency_ms_p95: 500
  primary_metric: {meta['primary_metric']}
  primary_score: {meta['primary_score']}

resource_requirements:
  min_cpu_cores: 2
  recommended_cpu_cores: 4
  min_memory_mb: 4096
  recommended_memory_mb: 8192
  min_gpu_count: 0
  gpu_type: optional
  min_vram_mb: 0
  recommended_vram_mb: 8192
  disk_mb: 4096

model_profile:
  parameter_count: 0
  parameter_count_text: varies_by_submodel
  flops: 0
  flops_text: varies_by_submodel
  precision: fp32

safety:
  risk_level: {meta['risk_level']}
  requires_human_review: true
"""
    (pkg / "algorithm_card.yaml").write_text(card, encoding="utf-8")
    (pkg / "README.md").write_text(
        f"# {meta['display_name']}\n\nTIA python_http_service package for `{algorithm_id}`.\n",
        encoding="utf-8",
    )
    (pkg / "service_contract.md").write_text(
        f"# Service Contract\n\n- GET /health\n- GET /metadata\n- POST /predict\n\nPort: {port}\n",
        encoding="utf-8",
    )
    request = {
        "request_id": "req_001",
        "trace_id": "trace_001",
        "algorithm_id": algorithm_id,
        "version": "1.0.0",
        "backend_type": "python_http_service",
        "inputs": GOLDEN_INPUTS[algorithm_id],
        "params": {},
    }
    response = {
        "ok": True,
        "request_id": "req_001",
        "trace_id": "trace_001",
        "algorithm_id": algorithm_id,
        "version": "1.0.0",
        "outputs": outputs,
        "usage": {"latency_ms": 1},
        "error": None,
    }
    golden = pkg / "golden_cases"
    golden.mkdir(parents=True, exist_ok=True)
    (golden / "case_001_request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    (golden / "case_001_response.json").write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    os_env = __import__("os")
    os_env.environ.setdefault("TIA_USE_MOCK", "1")
    for meta in TIA_ALGORITHMS:
        algorithm_id = meta["algorithm_id"]
        predict_fn = PREDICTOR_REGISTRY[algorithm_id]
        outputs = predict_fn(GOLDEN_INPUTS[algorithm_id], {})
        write_example_package(meta, outputs)
        write_service_main(meta)
        print(f"[OK] bootstrapped {algorithm_id}")
    print(f"Done. {len(TIA_ALGORITHMS)} TIA algorithm packages generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
