"""Stable requirement catalogs used by AMOS scripted scenarios.

The catalogs describe what a scenario is designed to exercise.  They never
claim that Commander selected a model or that an Agent executed successfully;
runtime evidence remains the responsibility of ``workflow_view``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


FUNCTIONAL_AGENT_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "agent_id": "A1",
        "name": "感知探测与认知共享 Agent",
        "responsibilities": ["探测", "识别", "分类", "定位", "验证", "信息发布"],
        "backend_roles": ["recon", "tactical_intelligence"],
        "activity_ids": ["multi_source_perception", "tactical_intelligence_fusion"],
    },
    {
        "agent_id": "A2",
        "name": "航迹跟踪与威胁评估 Agent",
        "responsibilities": ["航迹生成", "航迹维护", "威胁评估", "威胁排序"],
        "backend_roles": ["track_threat"],
        "activity_ids": ["track_and_assess", "retrack_after_execution"],
    },
    {
        "agent_id": "A3",
        "name": "任务调度与资源分配 Agent",
        "responsibilities": ["任务拆解", "资源匹配", "任务重分配"],
        "backend_roles": ["task_scheduling", "resource_allocation"],
        "activity_ids": ["schedule_and_allocate", "reschedule_resources"],
    },
    {
        "agent_id": "A4",
        "name": "方案规划与决策 Agent",
        "responsibilities": ["方案生成", "方案比较", "决策", "优先级确定"],
        "backend_roles": ["decision_planning"],
        "activity_ids": ["plan_and_decide", "replan"],
    },
    {
        "agent_id": "A5",
        "name": "规则、法律与授权约束 Agent",
        "responsibilities": ["交战规则", "授权条件", "附带损伤", "合规审查"],
        "backend_roles": ["compliance_authorization"],
        "activity_ids": ["review_constraints", "review_replan"],
    },
    {
        "agent_id": "A6",
        "name": "执行控制与闭环评估 Agent",
        "responsibilities": ["指令下发", "执行监控", "效果评估", "重规划"],
        "backend_roles": ["closed_loop"],
        "activity_ids": ["execute_and_assess", "close_loop"],
    },
)


ALGORITHM_CATALOG: tuple[dict[str, Any], ...] = (
    {"requirement_id": "M01", "algorithm_id": "clustering", "category": "基础算法", "name": "聚类算法", "assigned_agents": ["A1", "A2"]},
    {"requirement_id": "M02", "algorithm_id": "association", "category": "基础算法", "name": "关联算法", "assigned_agents": ["A3", "A4"]},
    {"requirement_id": "M03", "algorithm_id": "linear_regression", "category": "基础算法", "name": "线性回归模型", "assigned_agents": ["A2", "A3", "A6"]},
    {"requirement_id": "M04", "algorithm_id": "logistic_regression", "category": "基础算法", "name": "逻辑回归模型", "assigned_agents": ["A4", "A6"]},
    {"requirement_id": "M05", "algorithm_id": "random_forest", "category": "基础算法", "name": "随机森林模型", "assigned_agents": ["A2"]},
    {"requirement_id": "M06", "algorithm_id": "neural_network", "category": "基础算法", "name": "传统神经网络模型", "assigned_agents": ["A1"]},
    {"requirement_id": "M07", "algorithm_id": "naive_bayes_network", "category": "基础算法", "name": "朴素贝叶斯网络", "assigned_agents": ["A1", "A2"]},
    {"requirement_id": "M08", "algorithm_id": "generative_adversarial_network", "category": "知识增强", "name": "生成对抗网络", "assigned_agents": ["A1", "A6"]},
    {"requirement_id": "M09", "algorithm_id": "large_language_model", "category": "知识增强", "name": "大语言模型", "assigned_agents": ["A4"]},
    {"requirement_id": "M10", "algorithm_id": "retrieval_augmented_generation", "category": "知识增强", "name": "检索增强生成模型", "assigned_agents": ["A4", "A5"]},
    {"requirement_id": "M11", "algorithm_id": "agent_collaboration", "category": "分布式协同", "name": "智能体模型", "assigned_agents": ["A3", "A4"]},
    {"requirement_id": "M12", "algorithm_id": "federated_learning", "category": "分布式协同", "name": "联邦学习模型", "assigned_agents": ["A1", "A3"]},
    {"requirement_id": "M13", "algorithm_id": "reinforcement_learning", "category": "分布式协同", "name": "强化学习模型", "assigned_agents": ["A3", "A4"]},
    {"requirement_id": "M14", "algorithm_id": "explainable_ai", "category": "闭环支撑", "name": "可解释 AI 模型", "assigned_agents": ["A5", "A6"]},
    {"requirement_id": "M15", "algorithm_id": "multimodal_fusion", "category": "闭环支撑", "name": "多模态融合模型", "assigned_agents": ["A1"]},
    {"requirement_id": "M16", "algorithm_id": "time_series_prediction", "category": "闭环支撑", "name": "时间序列预测模型", "assigned_agents": ["A2", "A6"]},
)


ENGINEERING_MODEL_CATALOG: tuple[dict[str, Any], ...] = (
    {"requirement_id": "M17", "algorithm_id": "real_time_object_detection", "category": "工程模型", "name": "实时目标检测模型", "assigned_agents": ["A1"]},
    {"requirement_id": "M18", "algorithm_id": "change_detection", "category": "工程模型", "name": "差分检测与变化检测模型", "assigned_agents": ["A6"]},
    {"requirement_id": "M19", "algorithm_id": "multi_target_tracking", "category": "工程模型", "name": "多目标跟踪与定位模型", "assigned_agents": ["A1", "A2"]},
    {"requirement_id": "M20", "algorithm_id": "graph_neural_network", "category": "工程模型", "name": "图神经网络模型", "assigned_agents": ["A1", "A2", "A3"]},
)


MODEL_FUNCTION_POINTS: dict[str, list[str]] = {
    "M01": ["FP-01", "FP-04", "FP-11"],
    "M02": ["FP-15", "FP-17", "FP-20", "FP-26"],
    "M03": ["FP-13", "FP-15", "FP-19", "FP-25", "FP-27"],
    "M04": ["FP-20", "FP-25", "FP-28", "FP-29"],
    "M05": ["FP-12", "FP-13", "FP-18", "FP-27"],
    "M06": ["FP-01", "FP-05", "FP-06", "FP-07"],
    "M07": ["FP-06", "FP-07", "FP-13", "FP-16"],
    "M08": ["FP-02", "FP-03"],
    "M09": ["FP-17", "FP-20", "FP-22"],
    "M10": ["FP-16", "FP-17", "FP-21"],
    "M11": ["FP-15", "FP-17", "FP-20", "FP-22", "FP-26"],
    "M12": ["FP-10", "FP-14", "FP-24", "FP-26"],
    "M13": ["FP-15", "FP-20", "FP-26"],
    "M14": ["FP-09", "FP-16", "FP-21", "FP-25", "FP-28", "FP-29"],
    "M15": ["FP-04", "FP-05", "FP-06", "FP-07", "FP-08", "FP-09", "FP-10"],
    "M16": ["FP-11", "FP-14", "FP-19", "FP-24", "FP-27"],
    "M17": ["FP-01", "FP-02", "FP-03"],
    "M18": ["FP-02", "FP-25", "FP-27"],
    "M19": ["FP-08", "FP-11", "FP-14", "FP-24"],
    "M20": ["FP-10", "FP-11", "FP-14", "FP-18"],
}

_ACTIVITY_IDS_BY_AGENT = {
    str(item["agent_id"]): list(item["activity_ids"])
    for item in FUNCTIONAL_AGENT_CATALOG
}

MODEL_CATALOG: tuple[dict[str, Any], ...] = tuple(
    [
        dict(
            item,
            tier=tier,
            function_points=list(MODEL_FUNCTION_POINTS[str(item["requirement_id"])]),
            activity_ids=list(dict.fromkeys(
                activity_id
                for agent_id in item["assigned_agents"]
                for activity_id in _ACTIVITY_IDS_BY_AGENT[agent_id]
            )),
        )
        for tier, catalog in (
            ("core", ALGORITHM_CATALOG),
            ("engineering", ENGINEERING_MODEL_CATALOG),
        )
        for item in catalog
    ]
)


FUNCTION_POINT_CATALOG: tuple[dict[str, Any], ...] = (
    {"function_id": "FP-01", "name": "初始检测", "phase": "FIND"},
    {"function_id": "FP-02", "name": "战损评估检测", "phase": "FIND"},
    {"function_id": "FP-03", "name": "再次任务检测", "phase": "FIND"},
    {"function_id": "FP-04", "name": "定义目标", "phase": "FIX"},
    {"function_id": "FP-05", "name": "特征描述", "phase": "FIX"},
    {"function_id": "FP-06", "name": "分类", "phase": "FIX"},
    {"function_id": "FP-07", "name": "识别", "phase": "FIX"},
    {"function_id": "FP-08", "name": "定位", "phase": "FIX"},
    {"function_id": "FP-09", "name": "验证检测", "phase": "FIX"},
    {"function_id": "FP-10", "name": "分发目标或威胁信息", "phase": "TRACK"},
    {"function_id": "FP-11", "name": "生成或更新航迹", "phase": "TRACK"},
    {"function_id": "FP-12", "name": "威胁排序", "phase": "TRACK"},
    {"function_id": "FP-13", "name": "确定目标或威胁紧急度", "phase": "TRACK"},
    {"function_id": "FP-14", "name": "维持航迹", "phase": "TRACK"},
    {"function_id": "FP-15", "name": "评估兵力", "phase": "TARGET"},
    {"function_id": "FP-16", "name": "验证目标或威胁", "phase": "TARGET"},
    {"function_id": "FP-17", "name": "提名行动选项", "phase": "TARGET"},
    {"function_id": "FP-18", "name": "目标或威胁优先级排序", "phase": "TARGET"},
    {"function_id": "FP-19", "name": "确定可用时间", "phase": "TARGET"},
    {"function_id": "FP-20", "name": "挑选行动选项", "phase": "TARGET"},
    {"function_id": "FP-21", "name": "验证规则与授权", "phase": "ENGAGE"},
    {"function_id": "FP-22", "name": "下达指令", "phase": "ENGAGE"},
    {"function_id": "FP-23", "name": "执行任务", "phase": "ENGAGE"},
    {"function_id": "FP-24", "name": "跟踪执行状态", "phase": "ENGAGE"},
    {"function_id": "FP-25", "name": "确认效果", "phase": "ASSESS"},
    {"function_id": "FP-26", "name": "任务重分配", "phase": "ASSESS"},
    {"function_id": "FP-27", "name": "动态态势评估", "phase": "ASSESS"},
    {"function_id": "FP-28", "name": "任务结果判定", "phase": "ASSESS"},
    {"function_id": "FP-29", "name": "全局任务完成度评估", "phase": "ASSESS"},
)


def functional_agents() -> list[dict[str, Any]]:
    """Return the six document-defined Agent responsibilities in A1-A6 order."""
    return [{**deepcopy(item), "planned": True} for item in FUNCTIONAL_AGENT_CATALOG]


def planned_algorithms(*algorithm_ids: str) -> list[dict[str, Any]]:
    """Return selected core and engineering models without execution claims."""
    selected = set(algorithm_ids)
    return [
        {**deepcopy(item), "planned": True, "model_id": None, "version": None}
        for item in MODEL_CATALOG
        if item["algorithm_id"] in selected
    ]


def planned_function_points(*function_ids: str) -> list[dict[str, Any]]:
    """Return planned function-point records in catalog order."""
    selected = set(function_ids)
    return [
        {**deepcopy(item), "planned": True}
        for item in FUNCTION_POINT_CATALOG
        if item["function_id"] in selected
    ]


__all__ = [
    "ALGORITHM_CATALOG",
    "ENGINEERING_MODEL_CATALOG",
    "MODEL_CATALOG",
    "MODEL_FUNCTION_POINTS",
    "FUNCTIONAL_AGENT_CATALOG",
    "FUNCTION_POINT_CATALOG",
    "functional_agents",
    "planned_algorithms",
    "planned_function_points",
]
