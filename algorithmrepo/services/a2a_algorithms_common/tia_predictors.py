"""TIA 战术情报 Agent 子算法 — 算法库 python_http_service 推理入口。"""
from __future__ import annotations

import hashlib
import json
import math
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
    """与 TIA Agent 共用 load_config + compute profile，保证 HTTP 服务与 Agent 配置一致。"""
    try:
        from agent.pipeline import agent_config_from_yaml, load_config

        raw = load_config()
        agent_cfg = agent_config_from_yaml(raw)
    except Exception:
        cfg_path = ROOT / "config" / "default.yaml"
        if not cfg_path.is_file():
            return {"use_mock": _use_mock()}
        try:
            import yaml

            from agent.config_profiles import apply_compute_profile

            with open(cfg_path, encoding="utf-8") as handle:
                raw = yaml.safe_load(handle) or {}
            raw = apply_compute_profile(raw)
        except Exception:
            return {"use_mock": _use_mock()}
        agent_cfg = {
            "perception": (raw.get("skills") or {}).get("perception"),
            "cognition": (raw.get("skills") or {}).get("cognition"),
            "communication": (raw.get("skills") or {}).get("communication"),
            "inference": raw.get("inference") or {},
        }

    merged = dict(agent_cfg.get("inference") or {})
    for block_name in ("perception", "cognition", "communication"):
        block = agent_cfg.get(block_name) or {}
        if isinstance(block, dict):
            merged.update(block)
    merged["use_mock"] = _use_mock()
    return merged


_SUBSKILL_KEY_BY_MODULE: dict[str, str] = {
    "agent.skills.perception.marl_ppo_scheduler": "marl_ppo_scheduler",
    "agent.skills.perception.edl": "edl",
    "agent.skills.perception.rt_detr_odconv_detector": "rt_detr_odconv",
    "agent.skills.perception.siamese_mask2former_damage": "siamese_mask2former",
    "agent.skills.perception.motr_neural_kalman_tracker": "motr_neural_kalman",
    "agent.skills.cognition.imagebind_encoder": "imagebind",
    "agent.skills.cognition.multimodal_mamba": "multimodal_mamba",
    "agent.skills.cognition.supcon_meta_classifier": "supcon_meta",
    "agent.skills.cognition.synapse_rag": "synapse_rag",
    "agent.skills.communication.knowledge_semantic_comm": "knowledge_semantic_comm",
    "agent.skills.communication.marl_dynamic_router": "marl_dynamic_router",
}


def _backend_config(module_path: str) -> dict[str, Any]:
    from agent.skills.base import subskill_config

    base = _config()
    key = _SUBSKILL_KEY_BY_MODULE.get(module_path)
    if not key:
        return base
    return subskill_config(base, key)


@lru_cache(maxsize=1)
def _config() -> dict[str, Any]:
    return _load_config()


def _checkpoint_exists(name: str) -> bool:
    path = ROOT / "models" / "checkpoints" / name
    return path.is_file()


def _configured_checkpoint(config_key: str, default_name: str) -> Path:
    configured = str(_config().get(config_key) or default_name)
    path = Path(configured)
    candidates = [path, ROOT / path, ROOT / "models" / "checkpoints" / path.name]
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), candidates[-1].resolve())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _supcon_artifact_metadata() -> dict:
    checkpoint = _configured_checkpoint("supcon_checkpoint", "supcon_meta_s.safetensors")
    metadata_path = checkpoint.with_suffix(".metadata.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if _sha256(checkpoint) != metadata.get("artifact_sha256"):
        raise ValueError("SupCon checkpoint SHA256 mismatch")
    expected_dim = int(_config().get("embed_dim", 1024))
    if int(metadata.get("architecture", {}).get("input_dim", -1)) != expected_dim:
        raise ValueError("SupCon checkpoint input dimension does not match compute profile")
    return metadata


def _supcon_artifact_loaded() -> bool:
    try:
        _supcon_artifact_metadata()
        return True
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _supcon_artifact_identity() -> dict:
    metadata = _supcon_artifact_metadata()
    return {
        "model_id": metadata["model_id"],
        "model_version": metadata["model_version"],
        "model_family": metadata["model_family"],
        "compute_profile": metadata["compute_profile"],
        "artifact_sha256": metadata["artifact_sha256"],
    }


def _marl_ppo_artifact_metadata() -> dict:
    checkpoint = _configured_checkpoint(
        "marl_ppo_checkpoint", "marl_ppo_scheduler_s.safetensors"
    )
    metadata_path = checkpoint.with_suffix(".metadata.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if _sha256(checkpoint) != metadata.get("artifact_sha256"):
        raise ValueError("MARL-PPO checkpoint SHA256 mismatch")
    from agent.training.battlefield_scheduling_env import BattlefieldSchedulingEnv

    env = BattlefieldSchedulingEnv()
    architecture = metadata.get("architecture", {})
    expected = (env.obs_dim, env.n_actions, env.n_agents)
    actual = (
        int(architecture.get("observation_dimension", -1)),
        int(architecture.get("action_count", -1)),
        int(architecture.get("maximum_agent_count", -1)),
    )
    if actual != expected:
        raise ValueError("MARL-PPO checkpoint architecture does not match scheduling environment")
    return metadata


def _marl_ppo_artifact_loaded() -> bool:
    try:
        _marl_ppo_artifact_metadata()
        return True
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _marl_ppo_artifact_identity() -> dict:
    metadata = _marl_ppo_artifact_metadata()
    return {
        "model_id": metadata["model_id"],
        "model_version": metadata["model_version"],
        "model_family": metadata["model_family"],
        "compute_profile": metadata["compute_profile"],
        "artifact_sha256": metadata["artifact_sha256"],
    }


def _edl_artifact_metadata() -> dict:
    checkpoint = _configured_checkpoint("edl_checkpoint", "edl_head_s.safetensors")
    metadata_path = checkpoint.with_suffix(".metadata.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if _sha256(checkpoint) != metadata.get("artifact_sha256"):
        raise ValueError("EDL checkpoint SHA256 mismatch")
    architecture = metadata.get("architecture", {})
    if (
        int(architecture.get("input_dimension", -1)),
        int(architecture.get("class_count", -1)),
    ) != (6, 2):
        raise ValueError("EDL checkpoint architecture does not match verifier")
    return metadata


def _edl_artifact_loaded() -> bool:
    try:
        _edl_artifact_metadata()
        return True
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _edl_artifact_identity() -> dict:
    metadata = _edl_artifact_metadata()
    return {
        "model_id": metadata["model_id"],
        "model_version": metadata["model_version"],
        "model_family": metadata["model_family"],
        "compute_profile": metadata["compute_profile"],
        "artifact_sha256": metadata["artifact_sha256"],
    }


def _mamba_artifact_metadata() -> dict:
    checkpoint = _configured_checkpoint(
        "mamba_checkpoint", "mamba_fusion_s.safetensors"
    )
    metadata_path = checkpoint.with_suffix(".metadata.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if _sha256(checkpoint) != metadata.get("artifact_sha256"):
        raise ValueError("multimodal fusion checkpoint SHA256 mismatch")
    expected_dimension = int(_config().get("embed_dim", 1024))
    architecture = metadata.get("architecture", {})
    if int(architecture.get("embedding_dimension", -1)) != expected_dimension:
        raise ValueError("multimodal fusion checkpoint dimension does not match compute profile")
    return metadata


def _mamba_artifact_loaded() -> bool:
    try:
        _mamba_artifact_metadata()
        return True
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _mamba_artifact_identity() -> dict:
    metadata = _mamba_artifact_metadata()
    return {
        "model_id": metadata["model_id"],
        "model_version": metadata["model_version"],
        "model_family": metadata["model_family"],
        "compute_profile": metadata["compute_profile"],
        "artifact_sha256": metadata["artifact_sha256"],
    }


@lru_cache(maxsize=32)
def _backend(module_path: str, class_name: str):
    import importlib

    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(use_mock=_use_mock(), config=_backend_config(module_path))


def _run_backend(module_path: str, class_name: str, inputs: dict[str, Any]) -> Any:
    return _backend(module_path, class_name).run(inputs)


def tia_model_loaded(algorithm_id: str) -> bool:
    """Health check: mock mode always ready; real mode checks key weights."""
    if _use_mock():
        return True
    if algorithm_id == "supcon_meta_classifier":
        return _supcon_artifact_loaded()
    if algorithm_id == "marl_ppo_task_scheduler":
        return _marl_ppo_artifact_loaded()
    if algorithm_id == "edl_evidential_verifier":
        return _edl_artifact_loaded()
    if algorithm_id == "multimodal_mamba_fusion":
        return _mamba_artifact_loaded()
    checks: dict[str, list[str]] = {
        "battlefield_rtdetr_detector": ["battlefield_rtdetr.pt", "odconv_refiner.pt"],
        "siamese_mask2former_damage": [],
        "motr_neural_kalman_tracker": ["motr_tracker.pt", "motr_tracker_battlefield.pt"],
        "imagebind_multimodal_encoder": [],
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
    output = _run_backend(
        "agent.skills.perception.marl_ppo_scheduler",
        "MARLPPOScheduler",
        {
            "tracks": inputs.get("tracks") or [],
            "detections": inputs.get("detections") or [],
            "batch_context": inputs.get("batch_context") or {},
            "frames": inputs.get("frames") or [],
        },
    )
    if not _use_mock():
        output["model"] = _marl_ppo_artifact_identity()
    return output


def predict_edl_evidential_verifier(inputs: dict, params: dict) -> dict:
    assessments = _run_backend(
        "agent.skills.perception.edl",
        "EDLEvidentialVerifier",
        {
            "detections": inputs.get("detections") or [],
            "return_all_assessments": True,
        },
    )
    verified = [item for item in assessments if item.get("decision") == "verified"]
    rejected = [item for item in assessments if item.get("decision") == "rejected"]
    review_queue = [item for item in assessments if item.get("review_required") is True]
    output = {
        "assessments": assessments,
        "verified_detections": verified,
        "rejected_detections": rejected,
        "review_queue": review_queue,
        "count": len(verified),
        "summary": {
            "total": len(assessments),
            "verified": len(verified),
            "rejected": len(rejected),
            "manual_review": len(review_queue),
        },
    }
    if not _use_mock():
        output["model"] = _edl_artifact_identity()
    return output


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
    output = {"classifications": classifications, "count": len(classifications)}
    if not _use_mock():
        output["model"] = _supcon_artifact_identity()
    return output


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
    embeddings = inputs.get("embeddings") or {}
    if not isinstance(embeddings, dict) or not embeddings:
        raise ValueError("embeddings must be a non-empty object")
    for key, value in embeddings.items():
        if not isinstance(key, str) or not key:
            raise ValueError("embedding keys must be non-empty strings")
        if not isinstance(value, list) or not value:
            raise ValueError(f"embedding '{key}' must be a non-empty numeric array")
        if any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            for item in value
        ):
            raise ValueError(f"embedding '{key}' must contain only finite numbers")
    output = _run_backend(
        "agent.skills.cognition.multimodal_mamba",
        "MultimodalMambaFusion",
        {
            "embeddings": embeddings,
            "tracks": inputs.get("tracks") or [],
        },
    )
    output["modality_count"] = len(embeddings)
    output["input_embedding_dimensions"] = {
        key: len(value) for key, value in embeddings.items()
    }
    if not _use_mock():
        output["model"] = _mamba_artifact_identity()
    return output


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
