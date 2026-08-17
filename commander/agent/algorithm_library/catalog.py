"""TIA 算法目录与默认管线。"""

from __future__ import annotations

from typing import Any

from agent.algorithm_library.endpoints import (
    TIA_ALGORITHM_PORTS,
    TIA_ALGORITHM_VERSIONS,
    default_predict_endpoint,
)

TIA_ALLOWED_ALGORITHMS: set[str] = set(TIA_ALGORITHM_PORTS) - {"marl_ppo_task_scheduler"}

# 独立任务调度 Agent 白名单（经 algolib /algorithms 发现）
SCHEDULING_ALLOWED_ALGORITHMS: set[str] = {"marl_ppo_task_scheduler"}

TIA_DEFAULT_PIPELINE: list[str] = [
    "battlefield_rtdetr_detector",
    "siamese_mask2former_damage",
    "edl_evidential_verifier",
    "motr_neural_kalman_tracker",
    "multimodal_feature_fuser",
    "target_type_classifier",
    "track_state_updater",
    "trajectory_predictor",
    "graph_relation_reasoner",
    # marl_ppo_task_scheduler 已拆至独立 task_scheduling_agent，不在 TIA 管线内
    "imagebind_multimodal_encoder",
    "multimodal_mamba_fusion",
    "supcon_meta_classifier",
    "synapse_rag_retriever",
    "knowledge_semantic_comm",
    "marl_dynamic_router",
]

TIA_REQUIRED_ALGORITHMS: set[str] = {
    "battlefield_rtdetr_detector",
    "edl_evidential_verifier",
    "motr_neural_kalman_tracker",
}

# 算法 → 技能阶段
ALGORITHM_STAGE: dict[str, str] = {
    "battlefield_rtdetr_detector": "perception",
    "siamese_mask2former_damage": "perception",
    "edl_evidential_verifier": "perception",
    "motr_neural_kalman_tracker": "perception",
    "multimodal_feature_fuser": "perception",
    "target_type_classifier": "perception",
    "track_state_updater": "perception",
    "trajectory_predictor": "cognition",
    "graph_relation_reasoner": "cognition",
    "marl_ppo_task_scheduler": "planning",
    "imagebind_multimodal_encoder": "cognition",
    "multimodal_mamba_fusion": "cognition",
    "supcon_meta_classifier": "cognition",
    "synapse_rag_retriever": "cognition",
    "knowledge_semantic_comm": "communication",
    "marl_dynamic_router": "communication",
}

ALGORITHM_MODEL_IDS: dict[str, str] = {
    "marl_ppo_task_scheduler": "marl-ppo-scheduler",
    "battlefield_rtdetr_detector": "rt-detr-odconv-detector",
    "siamese_mask2former_damage": "siamese-mask2former-damage",
    "edl_evidential_verifier": "edl-evidential-verifier",
    "motr_neural_kalman_tracker": "motr-neural-kalman",
    "multimodal_feature_fuser": "multimodal-feature-fuser",
    "target_type_classifier": "target-type-classifier",
    "track_state_updater": "track-state-updater",
    "trajectory_predictor": "trajectory-predictor",
    "graph_relation_reasoner": "graph-relation-reasoner",
    "imagebind_multimodal_encoder": "imagebind-multimodal-encoder",
    "multimodal_mamba_fusion": "multimodal-mamba-fusion",
    "supcon_meta_classifier": "supcon-meta-classifier",
    "synapse_rag_retriever": "synapse-rag-retriever",
    "knowledge_semantic_comm": "knowledge-semantic-comm",
    "marl_dynamic_router": "marl-dynamic-router",
}

_ALGORITHM_CARDS: dict[str, dict[str, Any]] = {
    "marl_ppo_task_scheduler": {
        "task_family": "task_scheduling",
        "capabilities": ["sensor_tasking", "reattack_planning", "resource_allocation"],
        "summary": "MARL-PPO 传感器任务调度与再攻击规划",
        "required_fields": ["tracks", "batch_context"],
        "optional": False,
    },
    "battlefield_rtdetr_detector": {
        "task_family": "detection",
        "capabilities": ["eo_ir", "sar", "object_detection"],
        "summary": "战场目标检测（RT-DETR+ODConv）",
        "required_fields": ["frames"],
        "optional": False,
    },
    "siamese_mask2former_damage": {
        "task_family": "damage_assessment",
        "capabilities": ["bda", "mask"],
        "summary": "毁伤评估；无参考帧时可跳过",
        "required_fields": ["frames"],
        "optional": True,
    },
    "edl_evidential_verifier": {
        "task_family": "verification",
        "capabilities": ["uncertainty", "filter"],
        "summary": "证据深度学习检测验证",
        "required_fields": ["detections"],
        "optional": False,
    },
    "motr_neural_kalman_tracker": {
        "task_family": "tracking",
        "capabilities": ["multi_object_tracking", "geo"],
        "summary": "多目标跟踪与定位，产出 tracks",
        "required_fields": ["verified_detections"],
        "optional": False,
    },
    "multimodal_feature_fuser": {
        "task_family": "feature_engineering",
        "capabilities": ["multimodal_feature_fusion", "track_feature_enrichment"],
        "summary": "Track Threat Agent 多模态特征融合算法",
        "required_fields": ["observations"],
        "optional": True,
    },
    "target_type_classifier": {
        "task_family": "classification",
        "capabilities": ["structured_track_feature_classification", "object_type_completion"],
        "summary": "Track Threat Agent 目标类型分类算法",
        "required_fields": ["observations"],
        "optional": True,
    },
    "track_state_updater": {
        "task_family": "tracking",
        "capabilities": ["multi_target_tracking", "track_filtering", "nearest_neighbor_association"],
        "summary": "Track Threat Agent 航迹状态更新算法",
        "required_fields": ["detections"],
        "optional": True,
    },
    "trajectory_predictor": {
        "task_family": "forecasting",
        "capabilities": ["time_series_prediction", "st_gnn_candidate_prediction", "physics_baseline_fallback"],
        "summary": "Track Threat Agent 轨迹预测算法",
        "required_fields": ["tracks"],
        "optional": True,
    },
    "graph_relation_reasoner": {
        "task_family": "graph_reasoning",
        "capabilities": ["graph_neural_network", "relation_reasoning"],
        "summary": "Track Threat Agent 图关系推理算法",
        "required_fields": ["tracks"],
        "optional": True,
    },
    "imagebind_multimodal_encoder": {
        "task_family": "embedding",
        "capabilities": ["multimodal_embed"],
        "summary": "多模态嵌入；可跳过则分类降级",
        "required_fields": ["frames"],
        "optional": True,
    },
    "multimodal_mamba_fusion": {
        "task_family": "fusion",
        "capabilities": ["sequence_fusion"],
        "summary": "时序多模态融合；可跳过",
        "required_fields": ["embeddings", "tracks"],
        "optional": True,
    },
    "supcon_meta_classifier": {
        "task_family": "classification",
        "capabilities": ["affiliation", "few_shot"],
        "summary": "敌我/类别元学习分类；可跳过则用检测类名",
        "required_fields": ["fused_embeddings"],
        "optional": True,
    },
    "synapse_rag_retriever": {
        "task_family": "rag",
        "capabilities": ["knowledge_retrieval"],
        "summary": "知识检索；无 knowledge_base 时可跳过",
        "required_fields": ["classifications"],
        "optional": True,
    },
    "knowledge_semantic_comm": {
        "task_family": "compression",
        "capabilities": ["semantic_packet", "targets"],
        "summary": "语义压缩情报包；建议保留",
        "required_fields": ["perception", "cognition"],
        "optional": True,
    },
    "marl_dynamic_router": {
        "task_family": "routing",
        "capabilities": ["anti_jam_routing"],
        "summary": "下游路由；可跳过",
        "required_fields": ["packet"],
        "optional": True,
    },
}


def build_algorithm_catalog(
    *,
    host: str = "127.0.0.1",
    allowed: set[str] | None = None,
    include_scheduling: bool = False,
) -> list[dict[str, Any]]:
    """生成本地算法目录卡片，供小模型规划（对齐 lzh /algorithms 摘要）。"""
    allow = allowed or TIA_ALLOWED_ALGORITHMS
    catalog: list[dict[str, Any]] = []
    pipeline = list(TIA_DEFAULT_PIPELINE)
    if include_scheduling:
        # 调度算法不在 TIA 管线内，但网关需对外暴露给 task_scheduling_agent
        if "marl_ppo_task_scheduler" not in pipeline:
            pipeline = ["marl_ppo_task_scheduler", *pipeline]
        allow = set(allow) | SCHEDULING_ALLOWED_ALGORITHMS
    for algorithm_id in pipeline:
        if algorithm_id not in allow:
            continue
        if algorithm_id not in TIA_ALGORITHM_PORTS:
            continue
        card = _ALGORITHM_CARDS.get(algorithm_id, {})
        model_id = str(card.get("model_id") or ALGORITHM_MODEL_IDS.get(algorithm_id) or "")
        catalog.append(
            {
                "algorithm_id": algorithm_id,
                "version": TIA_ALGORITHM_VERSIONS.get(algorithm_id, "1.0.0"),
                "backend_type": "python_http_service",
                "task_family": card.get("task_family", ""),
                "model_id": model_id,
                "model_profile": {
                    "model_id": model_id,
                    "runtime": "python_http_service",
                } if model_id else {},
                "capabilities": card.get("capabilities", []),
                "required_fields": card.get("required_fields", []),
                "optional": bool(card.get("optional", True)),
                "summary": card.get("summary", ""),
                "agent_card": {"summary": card.get("summary", "")},
                "predict_endpoint": default_predict_endpoint(algorithm_id, host=host),
                "stage": ALGORITHM_STAGE.get(algorithm_id, ""),
            }
        )
    return catalog


def batch_context_summary(batch_context: dict[str, Any] | None) -> dict[str, Any]:
    """给 LLM 的精简任务上下文（不传图像字节）。"""
    ctx = batch_context or {}
    kb = ctx.get("knowledge_base") or []
    return {
        "command": ctx.get("command"),
        "has_reference_frame": bool(ctx.get("reference_frame")),
        "has_knowledge_base": bool(kb),
        "knowledge_base_count": len(kb) if isinstance(kb, list) else 0,
        "jamming_level": ctx.get("jamming_level", 0.0),
        "subscriber_agents": ctx.get("subscriber_agents") or [],
        "recon_report_present": bool(ctx.get("recon_report")),
        "sector": ctx.get("sector"),
    }
