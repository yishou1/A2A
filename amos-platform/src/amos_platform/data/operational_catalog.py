"""Canonical kill-chain, algorithm-class and Agent-skill presentation data.

This module deliberately separates the twenty business algorithm classes
(``M01``–``M20``) from deployable AlgoLib packages.  A package is an
implementation candidate, while a class is what the operator sees in the
requirements view.  In particular, an ONNX file being supplied does not mean
that the current server was built with ONNX Runtime support.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_FUNCTIONS = (
    ("KC-01", "初始探测", "Initial Detection", "observe", "find"), ("KC-02", "毁伤评估探测", "Battle Damage Assessment (BDA) Detection", "observe", "find"),
    ("KC-03", "再任务探测", "Re-Task Detection", "observe", "find"), ("KC-04", "定义目标/威胁", "Define Target / Threat", "observe", "fix"),
    ("KC-05", "特征刻画", "Characterize", "observe", "fix"), ("KC-06", "分类", "Classify", "observe", "fix"),
    ("KC-07", "识别", "Identify", "observe", "fix"), ("KC-08", "定位", "Locate", "observe", "fix"),
    ("KC-09", "验证探测", "Validate Detection", "observe", "fix"), ("KC-10", "分发目标/威胁信息", "Disseminate Target / Threat Information", "observe", "fix"),
    ("KC-11", "生成/更新航迹", "Generate / Update Track", "orient", "track"), ("KC-12", "排序", "Sort", "orient", "track"),
    ("KC-13", "判定目标/威胁紧迫性", "Determine Target / Threat Urgency", "orient", "track"), ("KC-14", "评估蓝方兵力接近度", "Assess Blue Force Proximity", "orient", "track"),
    ("KC-15", "验证目标/威胁", "Validate Target / Threat", "orient", "track"), ("KC-16", "提名交战选项", "Nominate Engagement Option", "decide", "target"),
    ("KC-17", "确定目标/威胁优先级", "Prioritize Target / Threat", "decide", "target"), ("KC-18", "判定可用时间", "Determine Time Available", "decide", "target"),
    ("KC-19", "保持航迹", "Maintain Track", "decide", "target"), ("KC-20", "选择攻击选项", "Select Attack Option", "decide", "target"),
    ("KC-21", "验证交战规则", "Verify Rules of Engagement (ROE)", "decide", "target"), ("KC-22", "下达命令", "Issue Order", "act", "engage"),
    ("KC-23", "打击目标/威胁", "Attack Target / Threat", "act", "engage"), ("KC-24", "跟踪武器", "Track Weapon", "act", "engage"),
    ("KC-25", "确认命中效果", "Confirm Impact", "act", "engage"), ("KC-26", "下达再攻击任务", "Task Re-Attack", "act", "engage"),
    ("KC-27", "实施动态评估", "Conduct Dynamic Assessment", "act", "assess"), ("KC-28", "评估", "Evaluate", "act", "assess"),
)

# These are workflow-level implementation packages rather than standalone
# M01-M20 business classes. Keep their ownership on the function record so
# the relationship view remains complete when AlgoLib is offline or has not
# returned an algorithm card yet.
_FUNCTION_PRIMARY_IMPLEMENTATIONS: dict[str, tuple[str, str]] = {
    "KC-04": ("mission_feature_adapter", "Mission Feature Adapter"),
    "KC-23": ("execution_control_planner", "Execution Control Planner"),
    "KC-28": ("mission_completion_scorer", "Mission Completion Scorer"),
}


FUNCTION_POINT_CATALOG: tuple[dict[str, Any], ...] = tuple(
    {"function_id": ident, "name": f"{chinese}（{english}）", "function_name": english,
     "chinese_name": chinese, "english_name": english, "ooda_phase": ooda,
     "f2t2ea_stage": stage, "phase": stage.upper(), "category": stage.upper(),
     "primary_algorithm_ids": ([_FUNCTION_PRIMARY_IMPLEMENTATIONS[ident][0]]
                                if ident in _FUNCTION_PRIMARY_IMPLEMENTATIONS else []),
     "primary_algorithm_names": ([_FUNCTION_PRIMARY_IMPLEMENTATIONS[ident][1]]
                                  if ident in _FUNCTION_PRIMARY_IMPLEMENTATIONS else [])}
    for ident, chinese, english, ooda, stage in _FUNCTIONS
)

# Exact implementation/package mapping checked against the active AlgoLib
# catalogue and examples.  The only non-business ONNX package,
# ``onnx_text_classifier``, is intentionally not represented here.
_CLASSES = (
    ("M01", "clustering", "聚类算法", "基础算法", ["clustering_engine"], [], [], ["KC-05", "KC-12"]),
    ("M02", "association", "关联算法", "基础算法", ["execution_rule_matcher"], [], [], ["KC-20", "KC-21"]),
    ("M03", "linear_regression", "线性回归模型", "基础算法", ["trajectory_linear_predictor"], ["trajectory_predictor"], [], ["KC-18", "KC-19", "KC-24"]),
    ("M04", "logistic_regression", "逻辑回归模型", "基础算法", ["decision_plan_recommender_onnx", "compliance_risk_scorer_onnx"], [], ["decision_plan_recommender_onnx", "compliance_risk_scorer_onnx"], ["KC-16", "KC-20", "KC-21"]),
    ("M05", "random_forest", "随机森林模型", "基础算法", ["threat_priority_random_forest"], [], ["threat_priority_random_forest_onnx"], ["KC-13", "KC-17"]),
    ("M06", "neural_network", "传统神经网络模型", "基础算法", ["supcon_meta_classifier"], ["target_type_classifier"], ["supcon_meta_classifier_onnx"], ["KC-06", "KC-07"]),
    ("M07", "naive_bayes_network", "朴素贝叶斯网络", "基础算法", ["intent_gaussian_naive_bayes"], [], ["intent_gaussian_naive_bayes_onnx"], ["KC-06", "KC-07"]),
    ("M08", "generative_adversarial_network", "生成对抗网络", "知识增强", ["conditional_tabular_gan"], [], ["conditional_tabular_gan_onnx"], []),
    ("M09", "large_language_model", "大语言模型", "知识增强", ["qwen3-1.7B"], [], [], []),
    ("M10", "retrieval_augmented_generation", "检索增强生成模型", "知识增强", ["synapse_rag_retriever"], ["knowledge_semantic_comm"], [], ["KC-10", "KC-15"]),
    ("M11", "agent_collaboration", "智能体模型", "分布式协同", ["decision_planning_core"], ["compliance_authorization_core"], [], ["KC-16", "KC-20", "KC-21"]),
    ("M12", "federated_learning", "联邦学习模型", "分布式协同", ["federated_fedavg_aggregator"], [], [], []),
    ("M13", "reinforcement_learning", "强化学习模型", "分布式协同", ["marl_ppo_task_scheduler"], ["marl_dynamic_router"], ["marl_ppo_task_scheduler_onnx"], ["KC-22", "KC-26"]),
    ("M14", "explainable_ai", "可解释 AI 模型", "闭环支撑", ["edl_evidential_verifier"], ["execution_rule_matcher"], ["edl_evidential_verifier_onnx"], ["KC-09", "KC-15"]),
    ("M15", "multimodal_fusion", "多模态融合模型", "闭环支撑", ["multimodal_mamba_fusion"], ["imagebind_multimodal_encoder", "multimodal_feature_fuser"], ["multimodal_mamba_fusion_onnx"], ["KC-05"]),
    ("M16", "time_series_prediction", "时间序列预测模型", "闭环支撑", ["trajectory_predictor"], ["trajectory_linear_predictor"], ["target_trend_predictor_onnx"], ["KC-18", "KC-19", "KC-24"]),
    ("M17", "real_time_object_detection", "实时目标检测模型", "工程模型", ["battlefield_rtdetr_detector"], [], [], ["KC-01", "KC-03"]),
    ("M18", "change_detection", "差分检测与变化检测模型", "工程模型", ["siamese_mask2former_damage", "xbd_damage_assessor"], [], [], ["KC-02", "KC-25", "KC-27"]),
    ("M19", "multi_target_tracking", "多目标跟踪与定位模型", "工程模型", ["motr_neural_kalman_tracker"], ["track_state_updater"], [], ["KC-08", "KC-11", "KC-19"]),
    ("M20", "graph_neural_network", "图神经网络模型", "工程模型", ["graph_relation_reasoner"], [], ["graph_relation_reasoner_onnx"], ["KC-05", "KC-14"]),
)

ALGORITHM_CLASS_CATALOG: tuple[dict[str, Any], ...] = tuple(
    {"requirement_id": req, "algorithm_class_id": req, "algorithm_id": algorithm_id, "name": name,
     "category": category, "primary_algorithm_ids": primary, "auxiliary_algorithm_ids": auxiliary,
     "onnx_package_ids": onnx, "onnx_provided": bool(onnx), "function_points": points,
     "delivery_status": "external" if req == "M09" else "available"}
    for req, algorithm_id, name, category, primary, auxiliary, onnx, points in _CLASSES
)

SKILL_ALGORITHM_BINDINGS: dict[str, dict[str, Any]] = {
    "tactical_intelligence_analysis": {"name": "战术情报分析", "agents": ["A1"], "primary": ["battlefield_rtdetr_detector", "multimodal_mamba_fusion"], "supporting": ["supcon_meta_classifier", "target_type_classifier", "edl_evidential_verifier"], "function_points": ["KC-01", "KC-04", "KC-05", "KC-06", "KC-07", "KC-09", "KC-10"]},
    "track_threat_situation_analysis": {"name": "航迹威胁态势分析", "agents": ["A2"], "primary": ["track_state_updater", "motr_neural_kalman_tracker", "threat_priority_random_forest"], "supporting": ["graph_relation_reasoner", "trajectory_predictor"], "function_points": ["KC-08", "KC-11", "KC-12", "KC-13", "KC-14", "KC-15", "KC-19"]},
    "task_scheduling_resource_allocation": {"name": "任务调度与资源分配", "agents": ["A3"], "primary": ["marl_ppo_task_scheduler"], "supporting": ["marl_dynamic_router"], "function_points": ["KC-16", "KC-22", "KC-26"]},
    "decision_planning_analysis": {"name": "方案规划与决策", "agents": ["A4"], "primary": ["decision_planning_core"], "supporting": ["closed_loop_decision_advisor"], "function_points": ["KC-16", "KC-17", "KC-18", "KC-20"]},
    "compliance_authorization_analysis": {"name": "合规与授权审查", "agents": ["A5"], "primary": ["compliance_authorization_core"], "supporting": ["execution_rule_matcher", "edl_evidential_verifier"], "function_points": ["KC-15", "KC-21"]},
    "execution_control": {"name": "执行控制", "agents": ["A6"], "primary": ["execution_control_planner"], "supporting": ["trajectory_predictor"], "function_points": ["KC-22", "KC-23", "KC-24", "KC-25"]},
    "closed_loop_optimization": {"name": "闭环优化", "agents": ["A6"], "primary": ["closed_loop_decision_advisor", "mission_completion_scorer"], "supporting": ["siamese_mask2former_damage", "xbd_damage_assessor"], "function_points": ["KC-02", "KC-25", "KC-26", "KC-27", "KC-28"]},
}


def operational_functions() -> list[dict[str, Any]]:
    return deepcopy(list(FUNCTION_POINT_CATALOG))


def algorithm_classes(*, onnx_runtime_available: bool = False) -> list[dict[str, Any]]:
    rows = deepcopy(list(ALGORITHM_CLASS_CATALOG))
    for row in rows:
        row["onnx_runtime_available"] = bool(onnx_runtime_available and row["onnx_provided"])
    return rows


def class_for_package(algorithm_id: str) -> dict[str, Any] | None:
    for item in ALGORITHM_CLASS_CATALOG:
        if algorithm_id in (*item["primary_algorithm_ids"], *item["auxiliary_algorithm_ids"], *item["onnx_package_ids"]):
            return deepcopy(item)
    return None


__all__ = ["FUNCTION_POINT_CATALOG", "ALGORITHM_CLASS_CATALOG", "SKILL_ALGORITHM_BINDINGS", "operational_functions", "algorithm_classes", "class_for_package"]
