"""TIA 战术情报 Agent 子算法 — 算法库 python_http_service 推理入口。"""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _use_mock() -> bool:
    return os.environ.get("TIA_USE_MOCK", "1") not in ("0", "false", "False")


def _load_config() -> dict[str, Any]:
    cfg_path = ROOT / "config" / "default.yaml"
    if not cfg_path.is_file():
        return {}
    try:
        import yaml

        with open(cfg_path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    except Exception:
        return {}
    inference = dict(raw.get("inference") or {})
    skills = raw.get("skills") or {}
    merged = {**inference}
    for block in skills.values():
        if isinstance(block, dict):
            merged.update(block)
    merged["use_mock"] = _use_mock()
    return merged


@lru_cache(maxsize=1)
def _config() -> dict[str, Any]:
    return _load_config()


def _checkpoint_exists(name: str) -> bool:
    path = ROOT / "models" / "checkpoints" / name
    return path.is_file()


@lru_cache(maxsize=32)
def _backend(module_path: str, class_name: str):
    import importlib

    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(use_mock=_use_mock(), config=_config())


def _run_backend(module_path: str, class_name: str, inputs: dict[str, Any]) -> Any:
    return _backend(module_path, class_name).run(inputs)


def tia_model_loaded(algorithm_id: str) -> bool:
    """Health check: mock mode always ready; real mode checks key weights."""
    if _use_mock():
        return True
    checks: dict[str, list[str]] = {
        "battlefield_rtdetr_detector": ["battlefield_rtdetr.pt", "odconv_refiner.pt"],
        "siamese_mask2former_damage": [],
        "edl_evidential_verifier": ["edl_head.pt"],
        "motr_neural_kalman_tracker": ["motr_tracker.pt", "motr_tracker_battlefield.pt"],
        "marl_ppo_task_scheduler": ["marl_ppo_scheduler.pt"],
        "imagebind_multimodal_encoder": [],
        "multimodal_mamba_fusion": ["mamba_fusion.pt"],
        "supcon_meta_classifier": ["supcon_meta.pt"],
        "synapse_rag_retriever": [],
        "knowledge_semantic_comm": [],
        "marl_dynamic_router": ["marl_policy.pt"],
    }
    required = checks.get(algorithm_id, [])
    if not required:
        return True
    return any(_checkpoint_exists(name) for name in required)


def predict_marl_ppo_task_scheduler(inputs: dict, params: dict) -> dict:
    return _run_backend(
        "agent.skills.perception.marl_ppo_scheduler",
        "MARLPPOScheduler",
        {
            "tracks": inputs.get("tracks") or [],
            "detections": inputs.get("detections") or [],
            "batch_context": inputs.get("batch_context") or {},
            "frames": inputs.get("frames") or [],
        },
    )


def predict_edl_evidential_verifier(inputs: dict, params: dict) -> dict:
    verified = _run_backend(
        "agent.skills.perception.edl",
        "EDLEvidentialVerifier",
        {"detections": inputs.get("detections") or []},
    )
    return {"verified_detections": verified, "count": len(verified)}


def predict_marl_dynamic_router(inputs: dict, params: dict) -> dict:
    return _run_backend(
        "agent.skills.communication.marl_dynamic_router",
        "MARLDynamicRouter",
        {
            "packet": inputs.get("packet") or {},
            "subscriber_agents": inputs.get("subscriber_agents") or [],
            "jamming_level": float(inputs.get("jamming_level", 0.0)),
        },
    )


def predict_supcon_meta_classifier(inputs: dict, params: dict) -> dict:
    classifications = _run_backend(
        "agent.skills.cognition.supcon_meta_classifier",
        "SupConMetaClassifier",
        {
            "fused_embeddings": inputs.get("fused_embeddings") or {},
            "support_shots": inputs.get("support_shots") or [],
        },
    )
    return {"classifications": classifications, "count": len(classifications)}


def predict_battlefield_rtdetr_detector(inputs: dict, params: dict) -> dict:
    frames = inputs.get("frames") or []
    if not frames and inputs.get("media_refs"):
        frames = [{"sensor_id": "MEDIA-0", "modality": "eo_ir", "payload": {"media_refs": inputs["media_refs"]}}]
    detections = _run_backend(
        "agent.skills.perception.rt_detr_odconv_detector",
        "RTDETRODConvDetector",
        {"frames": frames},
    )
    return {"detections": detections, "count": len(detections)}


def predict_siamese_mask2former_damage(inputs: dict, params: dict) -> dict:
    reports = _run_backend(
        "agent.skills.perception.siamese_mask2former_damage",
        "SiameseMask2FormerDamage",
        {
            "frames": inputs.get("frames") or [],
            "reference_frame": inputs.get("reference_frame"),
        },
    )
    return {"damage_reports": reports, "count": len(reports)}


def predict_motr_neural_kalman_tracker(inputs: dict, params: dict) -> dict:
    return _run_backend(
        "agent.skills.perception.motr_neural_kalman_tracker",
        "MOTRNeuralKalmanTracker",
        {
            "verified_detections": inputs.get("verified_detections") or [],
            "prior_tracks": inputs.get("prior_tracks") or [],
            "visual_frame": inputs.get("visual_frame"),
            "batch_context": inputs.get("batch_context") or {},
        },
    )


def predict_imagebind_multimodal_encoder(inputs: dict, params: dict) -> dict:
    embeddings = _run_backend(
        "agent.skills.cognition.imagebind_encoder",
        "ImageBindEncoder",
        {"frames": inputs.get("frames") or []},
    )
    return {"embeddings": embeddings, "count": len(embeddings)}


def predict_multimodal_mamba_fusion(inputs: dict, params: dict) -> dict:
    return _run_backend(
        "agent.skills.cognition.multimodal_mamba",
        "MultimodalMambaFusion",
        {
            "embeddings": inputs.get("embeddings") or {},
            "tracks": inputs.get("tracks") or [],
        },
    )


def predict_synapse_rag_retriever(inputs: dict, params: dict) -> dict:
    return _run_backend(
        "agent.skills.cognition.synapse_rag",
        "SynapseRAG",
        {
            "classifications": inputs.get("classifications") or [],
            "knowledge_base": inputs.get("knowledge_base") or [],
            "query": inputs.get("query") or "战场目标实体与威胁关联",
        },
    )


def predict_knowledge_semantic_comm(inputs: dict, params: dict) -> dict:
    return _run_backend(
        "agent.skills.communication.knowledge_semantic_comm",
        "KnowledgeSemanticCommModel",
        {
            "perception": inputs.get("perception") or {},
            "cognition": inputs.get("cognition") or {},
        },
    )


PREDICTOR_REGISTRY: dict[str, Callable[[dict, dict], dict]] = {
    "marl_ppo_task_scheduler": predict_marl_ppo_task_scheduler,
    "edl_evidential_verifier": predict_edl_evidential_verifier,
    "marl_dynamic_router": predict_marl_dynamic_router,
    "supcon_meta_classifier": predict_supcon_meta_classifier,
    "battlefield_rtdetr_detector": predict_battlefield_rtdetr_detector,
    "siamese_mask2former_damage": predict_siamese_mask2former_damage,
    "motr_neural_kalman_tracker": predict_motr_neural_kalman_tracker,
    "imagebind_multimodal_encoder": predict_imagebind_multimodal_encoder,
    "multimodal_mamba_fusion": predict_multimodal_mamba_fusion,
    "synapse_rag_retriever": predict_synapse_rag_retriever,
    "knowledge_semantic_comm": predict_knowledge_semantic_comm,
}
