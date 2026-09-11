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

ORCHESTRATION_ALLOWED_ALGORITHMS: set[str] = {
    "execution_rule_matcher",
    "trajectory_linear_predictor",
    "decision_planning_core",
    "compliance_authorization_core",
    "execution_control_planner",
    "mission_feature_adapter",
    "mission_completion_scorer",
    "closed_loop_decision_advisor",
    "xbd_damage_assessor",
    "clustering_engine",
    "threat_priority_random_forest",
    "intent_gaussian_naive_bayes",
    "federated_fedavg_aggregator",
    "conditional_tabular_gan",
}

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
    "execution_rule_matcher": "authorization",
    "trajectory_linear_predictor": "cognition",
    "decision_planning_core": "planning",
    "compliance_authorization_core": "authorization",
    "execution_control_planner": "execution",
    "mission_feature_adapter": "closed_loop",
    "mission_completion_scorer": "closed_loop",
    "closed_loop_decision_advisor": "closed_loop",
    "xbd_damage_assessor": "assessment",
    "clustering_engine": "perception",
    "threat_priority_random_forest": "cognition",
    "intent_gaussian_naive_bayes": "cognition",
    "federated_fedavg_aggregator": "coordination",
    "conditional_tabular_gan": "generation",
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
    "execution_rule_matcher": "execution-rule-matcher",
    "trajectory_linear_predictor": "trajectory-linear-predictor",
    "decision_planning_core": "decision-planning-core",
    "compliance_authorization_core": "compliance-authorization-core",
    "execution_control_planner": "execution-control-planner",
    "mission_feature_adapter": "mission-feature-adapter",
    "mission_completion_scorer": "mission-completion-scorer",
    "closed_loop_decision_advisor": "closed-loop-decision-advisor",
    "xbd_damage_assessor": "xbd-damage-assessor",
    "clustering_engine": "clustering-engine",
    "threat_priority_random_forest": "threat-priority-random-forest",
    "intent_gaussian_naive_bayes": "intent-gaussian-naive-bayes",
    "federated_fedavg_aggregator": "federated-fedavg-aggregator",
    "conditional_tabular_gan": "conditional-tabular-gan",
}

ALGORITHM_MODEL_PROFILES: dict[str, dict[str, Any]] = {
    "marl_ppo_task_scheduler": {
        "parameter_count": 39178,
        "parameter_count_text": "39K (MARLPPOSchedulerNet obs_dim=102 hidden=128)",
        "flops": 78000,
        "flops_text": "~78 KFLOPs per agent observation (encoder+actor+critic)",
        "flops_input_shape": [1, 102],
        "model_size_mb": 1,
        "precision": "fp32",
    },
    "battlefield_rtdetr_detector": {
        "parameter_count": 32024000,
        "parameter_count_text": "32.0M (RT-DETR-L 32M + ODConv refiner 24K)",
        "flops": 91900000000,
        "flops_text": "91.9 GFLOPs @ 640x640 (RT-DETR-L official); ODConv adds ~5 MFLOPs per crop",
        "flops_input_shape": [1, 3, 640, 640],
        "model_size_mb": 122,
        "precision": "fp32",
    },
    "siamese_mask2former_damage": {
        "parameter_count": 44000000,
        "parameter_count_text": "44M (Mask2Former-Swin-Tiny Siamese)",
        "flops": 55000000000,
        "flops_text": "~55 GFLOPs @ 512x512 per image (single branch, doubled for siamese pair)",
        "flops_input_shape": [1, 3, 512, 512],
        "model_size_mb": 168,
        "precision": "fp32",
    },
    "edl_evidential_verifier": {
        "parameter_count": 290,
        "parameter_count_text": "290",
        "flops": 580,
        "flops_text": "~580 FLOPs per detection sample (2 linear layers)",
        "flops_input_shape": [1, 6],
        "model_size_mb": 1,
        "precision": "fp32",
    },
    "motr_neural_kalman_tracker": {
        "parameter_count": 12296000,
        "parameter_count_text": "12.3M (MOTRCostNet ResNet18+Transformer + KalmanNet GRU)",
        "flops": 1800000000,
        "flops_text": "~1.8 GFLOPs per frame @ 224x224 crop (ResNet18 backbone)",
        "flops_input_shape": [1, 3, 224, 224],
        "model_size_mb": 48,
        "precision": "fp32",
    },
    "multimodal_feature_fuser": {
        "parameter_count": 0,
        "parameter_count_text": "deterministic feature adapter",
        "model_size_mb": 0,
        "precision": "fp32",
    },
    "target_type_classifier": {
        "parameter_count": 0,
        "parameter_count_text": "rule-based",
        "model_size_mb": 0,
        "precision": "fp32",
    },
    "track_state_updater": {
        "parameter_count": 0,
        "parameter_count_text": "nearest-neighbor plus lightweight filter",
        "model_size_mb": 0,
        "precision": "fp32",
    },
    "trajectory_predictor": {
        "parameter_count": 0,
        "parameter_count_text": "frozen TorchScript bundles, see models/track_threat",
        "model_size_mb": 2,
        "precision": "fp32",
    },
    "graph_relation_reasoner": {
        "parameter_count": 0,
        "parameter_count_text": "graph-rule reasoner; ST-GNN model bundles are stored separately",
        "model_size_mb": 2,
        "precision": "fp32",
    },
    "imagebind_multimodal_encoder": {
        "parameter_count": 632000000,
        "parameter_count_text": "632M (ImageBind-Huge ViT-H)",
        "flops": 1000000000000,
        "flops_text": "~1.0 TFLOPs @ 224x224 per modality (ViT-H)",
        "flops_input_shape": [1, 3, 224, 224],
        "model_size_mb": 2413,
        "precision": "fp32",
    },
    "multimodal_mamba_fusion": {
        "parameter_count": 6370000,
        "parameter_count_text": "6.37M (MultimodalMambaBlock d_model=1024 medium profile)",
        "flops": 50000000,
        "flops_text": "~50 MFLOPs per sequence step (d_model=1024, seq_len=8)",
        "flops_input_shape": [1, 8, 1024],
        "model_size_mb": 24,
        "precision": "fp32",
    },
    "supcon_meta_classifier": {
        "parameter_count": 295808,
        "parameter_count_text": "296K (SupConMetaNet in_dim=1024 medium profile)",
        "flops": 590000,
        "flops_text": "~590 KFLOPs per target embedding (2 linear layers + cosine sim)",
        "flops_input_shape": [1, 1024],
        "model_size_mb": 2,
        "precision": "fp32",
    },
    "synapse_rag_retriever": {
        "parameter_count": 0,
        "parameter_count_text": "varies_by_submodel",
        "flops": 0,
        "flops_text": "varies_by_submodel",
        "precision": "fp32",
    },
    "knowledge_semantic_comm": {
        "parameter_count": 0,
        "parameter_count_text": "varies_by_submodel",
        "flops": 0,
        "flops_text": "varies_by_submodel",
        "precision": "fp32",
    },
    "marl_dynamic_router": {
        "parameter_count": 0,
        "parameter_count_text": "varies_by_submodel",
        "flops": 0,
        "flops_text": "varies_by_submodel",
        "precision": "fp32",
    },
    "execution_rule_matcher": {
        "parameter_count": 0,
        "parameter_count_text": "mined execution-rule matcher",
        "model_size_mb": 0,
        "precision": "fp32",
    },
    "trajectory_linear_predictor": {
        "parameter_count": 0,
        "parameter_count_text": "linear trajectory predictor",
        "model_size_mb": 0,
        "precision": "fp32",
    },
    "decision_planning_core": {
        "parameter_count": 0,
        "parameter_count_text": "deterministic decision-planning runtime",
        "model_size_mb": 0,
        "precision": "fp32",
    },
    "compliance_authorization_core": {
        "parameter_count": 0,
        "parameter_count_text": "deterministic compliance runtime",
        "model_size_mb": 0,
        "precision": "fp32",
    },
    "execution_control_planner": {
        "parameter_count": 0,
        "parameter_count_text": "deterministic execution-control planner",
        "model_size_mb": 0,
        "precision": "fp32",
    },
    "mission_feature_adapter": {
        "parameter_count": 0,
        "parameter_count_text": "deterministic mission feature adapter",
        "model_size_mb": 0,
        "precision": "fp32",
    },
    "mission_completion_scorer": {
        "parameter_count": 0,
        "parameter_count_text": "mission completion scoring runtime",
        "model_size_mb": 0,
        "precision": "fp32",
    },
    "closed_loop_decision_advisor": {
        "parameter_count": 0,
        "parameter_count_text": "deterministic closed-loop advisor",
        "model_size_mb": 0,
        "precision": "fp32",
    },
    "xbd_damage_assessor": {
        "parameter_count": 0,
        "parameter_count_text": "xBD damage assessment runtime",
        "model_size_mb": 0,
        "precision": "fp32",
    },
    "clustering_engine": {
        "parameter_count": 0,
        "parameter_count_text": "classical clustering runtime",
        "model_size_mb": 0,
        "precision": "fp32",
    },
    "threat_priority_random_forest": {
        "parameter_count": 0,
        "parameter_count_text": "random forest threat priority model",
        "model_size_mb": 0,
        "precision": "fp32",
    },
    "intent_gaussian_naive_bayes": {
        "parameter_count": 0,
        "parameter_count_text": "Gaussian naive Bayes intent classifier",
        "model_size_mb": 0,
        "precision": "fp32",
    },
    "federated_fedavg_aggregator": {
        "parameter_count": 0,
        "parameter_count_text": "FedAvg reference aggregator",
        "model_size_mb": 0,
        "precision": "fp32",
    },
    "conditional_tabular_gan": {
        "parameter_count": 0,
        "parameter_count_text": "conditional tabular GAN runtime",
        "model_size_mb": 0,
        "precision": "fp32",
    },
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
    "execution_rule_matcher": {
        "task_family": "decision",
        "capabilities": ["rule_matching", "execution_constraint_lookup"],
        "summary": "Execution rule matcher algorithm",
        "required_fields": ["rules", "facts"],
        "optional": False,
    },
    "trajectory_linear_predictor": {
        "task_family": "forecasting",
        "capabilities": ["linear_motion_prediction", "trajectory_forecast"],
        "summary": "Linear trajectory prediction algorithm",
        "required_fields": ["tracks"],
        "optional": False,
    },
    "decision_planning_core": {
        "task_family": "decision_planning",
        "capabilities": ["candidate_plan_generation", "engagement_option_scoring"],
        "summary": "Decision Planning Agent core planning algorithm",
        "required_fields": ["scheduled_tasks", "resources"],
        "optional": False,
    },
    "compliance_authorization_core": {
        "task_family": "authorization",
        "capabilities": ["roe_check", "authorization_gate", "evidence_review"],
        "summary": "Compliance Authorization Agent rule and evidence review algorithm",
        "required_fields": ["candidate_plans", "authorization"],
        "optional": False,
    },
    "execution_control_planner": {
        "task_family": "execution_control",
        "capabilities": ["execution_command_generation", "fire_control_preparation"],
        "summary": "Execution Control Agent planner algorithm",
        "required_fields": ["phase", "results"],
        "optional": False,
    },
    "mission_feature_adapter": {
        "task_family": "feature_engineering",
        "capabilities": ["mission_feature_vectorization", "result_normalization"],
        "summary": "Closed-loop mission feature adapter",
        "required_fields": ["results"],
        "optional": False,
    },
    "mission_completion_scorer": {
        "task_family": "scoring",
        "capabilities": ["mission_completion_scoring", "objective_progress"],
        "summary": "Closed-loop mission completion scoring algorithm",
        "required_fields": ["features"],
        "optional": False,
    },
    "closed_loop_decision_advisor": {
        "task_family": "closed_loop_decision",
        "capabilities": ["replan_advice", "target_followup_action"],
        "summary": "Closed-loop decision advisor algorithm",
        "required_fields": ["targets", "mission_score"],
        "optional": False,
    },
    "xbd_damage_assessor": {
        "task_family": "damage_assessment",
        "capabilities": ["battle_damage_assessment", "damage_probability"],
        "summary": "xBD-style battle damage assessment algorithm",
        "required_fields": ["targets"],
        "optional": False,
    },
    "clustering_engine": {
        "task_family": "clustering",
        "capabilities": ["contact_clustering", "group_discovery"],
        "summary": "Clustering engine algorithm",
        "required_fields": ["points"],
        "optional": False,
    },
    "threat_priority_random_forest": {
        "task_family": "classification",
        "capabilities": ["threat_priority_scoring", "risk_ranking"],
        "summary": "Random forest threat priority algorithm",
        "required_fields": ["features"],
        "optional": False,
    },
    "intent_gaussian_naive_bayes": {
        "task_family": "classification",
        "capabilities": ["intent_classification", "probabilistic_classification"],
        "summary": "Gaussian naive Bayes intent classification algorithm",
        "required_fields": ["features"],
        "optional": False,
    },
    "federated_fedavg_aggregator": {
        "task_family": "federated_learning",
        "capabilities": ["federated_weight_aggregation", "fedavg"],
        "summary": "Federated FedAvg aggregation algorithm",
        "required_fields": ["client_updates"],
        "optional": False,
    },
    "conditional_tabular_gan": {
        "task_family": "generation",
        "capabilities": ["synthetic_tabular_generation", "conditional_generation"],
        "summary": "Conditional tabular GAN algorithm",
        "required_fields": ["conditions"],
        "optional": False,
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
        for algorithm_id in ORCHESTRATION_ALLOWED_ALGORITHMS:
            if algorithm_id not in pipeline:
                pipeline.append(algorithm_id)
        allow = set(allow) | SCHEDULING_ALLOWED_ALGORITHMS | ORCHESTRATION_ALLOWED_ALGORITHMS
    for algorithm_id in pipeline:
        if algorithm_id not in allow:
            continue
        if algorithm_id not in TIA_ALGORITHM_PORTS:
            continue
        card = _ALGORITHM_CARDS.get(algorithm_id, {})
        model_id = str(card.get("model_id") or ALGORITHM_MODEL_IDS.get(algorithm_id) or "")
        model_profile = dict(ALGORITHM_MODEL_PROFILES.get(algorithm_id, {}))
        if model_id:
            model_profile["model_id"] = model_id
            model_profile["runtime"] = "python_http_service"
        catalog.append(
            {
                "algorithm_id": algorithm_id,
                "version": TIA_ALGORITHM_VERSIONS.get(algorithm_id, "1.0.0"),
                "backend_type": "python_http_service",
                "task_family": card.get("task_family", ""),
                "model_id": model_id,
                "model_profile": model_profile,
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
