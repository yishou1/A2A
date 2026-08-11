"""按 execution_mode 创建本地或算法库远程后端。"""

from __future__ import annotations

import os
from typing import Any

from agent.algorithm_library.remote_backend import RemoteAlgorithmBackend
from agent.skills.base import subskill_config


def execution_mode(config: dict[str, Any] | None) -> str:
    env_mode = os.environ.get("TIA_EXECUTION_MODE", "").strip().lower()
    if env_mode in ("algorithm_library", "in_process"):
        return env_mode
    cfg = config or {}
    lib = cfg.get("algorithm_library") or {}
    if lib.get("enabled") is False:
        return "in_process"
    return str(cfg.get("execution_mode") or "algorithm_library")


def _use_library(config: dict[str, Any] | None) -> bool:
    return execution_mode(config) == "algorithm_library"


def create_rt_detr_detector(*, use_mock: bool, config: dict[str, Any] | None):
    if _use_library(config):
        return RemoteAlgorithmBackend(
            algorithm_id="battlefield_rtdetr_detector",
            name="RT-DETR+ODConv",
            config=config,
            output_key="detections",
        )
    from agent.skills.perception.rt_detr_odconv_detector import RTDETRODConvDetector

    return RTDETRODConvDetector(use_mock=use_mock, config=subskill_config(config, "rt_detr_odconv"))


def create_damage_assessor(*, use_mock: bool, config: dict[str, Any] | None):
    if _use_library(config):
        return RemoteAlgorithmBackend(
            algorithm_id="siamese_mask2former_damage",
            name="Siamese-Mask2Former",
            config=config,
            output_key="damage_reports",
        )
    from agent.skills.perception.siamese_mask2former_damage import SiameseMask2FormerDamage

    return SiameseMask2FormerDamage(
        use_mock=use_mock, config=subskill_config(config, "siamese_mask2former")
    )


def create_edl_verifier(*, use_mock: bool, config: dict[str, Any] | None):
    if _use_library(config):
        return RemoteAlgorithmBackend(
            algorithm_id="edl_evidential_verifier",
            name="EDL-Evidential-Deep-Learning",
            config=config,
            output_key="verified_detections",
        )
    from agent.skills.perception.edl import EDLEvidentialVerifier

    return EDLEvidentialVerifier(use_mock=use_mock, config=subskill_config(config, "edl"))


def create_motr_tracker(*, use_mock: bool, config: dict[str, Any] | None):
    if _use_library(config):
        return RemoteAlgorithmBackend(
            algorithm_id="motr_neural_kalman_tracker",
            name="MOTR+Neural-Kalman",
            config=config,
            output_key=None,
        )
    from agent.skills.perception.motr_neural_kalman_tracker import MOTRNeuralKalmanTracker

    return MOTRNeuralKalmanTracker(
        use_mock=use_mock, config=subskill_config(config, "motr_neural_kalman")
    )


def create_marl_ppo_scheduler(*, use_mock: bool, config: dict[str, Any] | None):
    if _use_library(config):
        return RemoteAlgorithmBackend(
            algorithm_id="marl_ppo_task_scheduler",
            name="MARL-PPO-Task-Scheduler",
            config=config,
            output_key=None,
        )
    from agent.skills.perception.marl_ppo_scheduler import MARLPPOScheduler

    return MARLPPOScheduler(use_mock=use_mock, config=subskill_config(config, "marl_ppo_scheduler"))


def create_imagebind_encoder(*, use_mock: bool, config: dict[str, Any] | None):
    if _use_library(config):
        return RemoteAlgorithmBackend(
            algorithm_id="imagebind_multimodal_encoder",
            name="ImageBind-CrossModal",
            config=config,
            output_key="embeddings",
        )
    from agent.skills.cognition.imagebind_encoder import ImageBindEncoder

    return ImageBindEncoder(use_mock=use_mock, config=subskill_config(config, "imagebind"))


def create_mamba_fusion(*, use_mock: bool, config: dict[str, Any] | None):
    if _use_library(config):
        return RemoteAlgorithmBackend(
            algorithm_id="multimodal_mamba_fusion",
            name="Multimodal-Mamba",
            config=config,
            output_key=None,
        )
    from agent.skills.cognition.multimodal_mamba import MultimodalMambaFusion

    return MultimodalMambaFusion(use_mock=use_mock, config=subskill_config(config, "multimodal_mamba"))


def create_supcon_classifier(*, use_mock: bool, config: dict[str, Any] | None):
    if _use_library(config):
        return RemoteAlgorithmBackend(
            algorithm_id="supcon_meta_classifier",
            name="SupCon+Meta-Learning",
            config=config,
            output_key="classifications",
        )
    from agent.skills.cognition.supcon_meta_classifier import SupConMetaClassifier

    return SupConMetaClassifier(use_mock=use_mock, config=subskill_config(config, "supcon_meta"))


def create_synapse_rag(*, use_mock: bool, config: dict[str, Any] | None):
    if _use_library(config):
        return RemoteAlgorithmBackend(
            algorithm_id="synapse_rag_retriever",
            name="SynapseRAG",
            config=config,
            output_key=None,
        )
    from agent.skills.cognition.synapse_rag import SynapseRAG

    return SynapseRAG(use_mock=use_mock, config=subskill_config(config, "synapse_rag"))


def create_semantic_comm(*, use_mock: bool, config: dict[str, Any] | None):
    if _use_library(config):
        return RemoteAlgorithmBackend(
            algorithm_id="knowledge_semantic_comm",
            name="Knowledge-Semantic-Comm",
            config=config,
            output_key=None,
        )
    from agent.skills.communication.knowledge_semantic_comm import KnowledgeSemanticCommModel

    return KnowledgeSemanticCommModel(
        use_mock=use_mock, config=subskill_config(config, "knowledge_semantic_comm")
    )


def create_marl_router(*, use_mock: bool, config: dict[str, Any] | None):
    if _use_library(config):
        return RemoteAlgorithmBackend(
            algorithm_id="marl_dynamic_router",
            name="MARL-Dynamic-Routing",
            config=config,
            output_key=None,
        )
    from agent.skills.communication.marl_dynamic_router import MARLDynamicRouter

    return MARLDynamicRouter(use_mock=use_mock, config=subskill_config(config, "marl_dynamic_router"))
