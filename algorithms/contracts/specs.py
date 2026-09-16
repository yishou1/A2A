"""各算法的规范 I/O 契约（方案 A：算法只认自己的输入/输出键）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AlgorithmContract:
    """单个算法的契约描述。"""

    algorithm_id: str
    required_inputs: tuple[str, ...]
    optional_inputs: tuple[str, ...] = ()
    # predict() 返回 dict 时必须出现的主键（至少一个）
    output_keys: tuple[str, ...] = ()
    # 本地 backend.run() 若返回 list/裸对象，归一化时写入该键
    run_unwrap_key: str | None = None
    description: str = ""
    # 输入别名：外部键 -> 契约键（适配器用）
    input_aliases: dict[str, str] = field(default_factory=dict)

    def accepts_input_key(self, key: str) -> bool:
        return key in self.required_inputs or key in self.optional_inputs


CONTRACTS: dict[str, AlgorithmContract] = {
    "battlefield_rtdetr_detector": AlgorithmContract(
        algorithm_id="battlefield_rtdetr_detector",
        required_inputs=("frames",),
        optional_inputs=("media_refs",),
        output_keys=("detections",),
        run_unwrap_key="detections",
        description="战场目标检测（RT-DETR±ODConv；可关 ODConv/半精度/Top-K）",
        input_aliases={"images": "frames", "image_frames": "frames"},
    ),
    "siamese_mask2former_damage": AlgorithmContract(
        algorithm_id="siamese_mask2former_damage",
        required_inputs=("frames",),
        optional_inputs=("reference_frame",),
        output_keys=("damage_reports",),
        run_unwrap_key="damage_reports",
        description="孪生毁伤评估（damage_backend/param_tier：low→轻量CNN）",
    ),
    "edl_evidential_verifier": AlgorithmContract(
        algorithm_id="edl_evidential_verifier",
        required_inputs=("detections",),
        optional_inputs=(),
        output_keys=("verified_detections", "assessments", "summary"),
        run_unwrap_key="verified_detections",
        description="EDL 三态门控（verified/rejected/manual_review）",
        input_aliases={"candidates": "detections", "raw_detections": "detections"},
    ),
    "motr_neural_kalman_tracker": AlgorithmContract(
        algorithm_id="motr_neural_kalman_tracker",
        required_inputs=("verified_detections",),
        optional_inputs=("prior_tracks", "visual_frame", "batch_context"),
        output_keys=("tracks",),
        run_unwrap_key=None,
        description="MOTR风格+KalmanNet 多目标跟踪（motr_variant=low|mid|high）",
        input_aliases={"detections": "verified_detections"},
    ),
    "marl_ppo_task_scheduler": AlgorithmContract(
        algorithm_id="marl_ppo_task_scheduler",
        required_inputs=(),
        optional_inputs=("tracks", "detections", "batch_context", "frames", "amos_payload"),
        output_keys=("sensor_assignments", "reattack_plan", "assignments", "schedule"),
        run_unwrap_key=None,
        description="MARL-PPO 任务调度（独立智能体）",
    ),
    "imagebind_multimodal_encoder": AlgorithmContract(
        algorithm_id="imagebind_multimodal_encoder",
        required_inputs=("frames",),
        optional_inputs=(),
        output_keys=("embeddings",),
        run_unwrap_key="embeddings",
        description="多模态编码（low=MobileNet/mid=ResNet18/high=ImageBind）",
    ),
    "multimodal_mamba_fusion": AlgorithmContract(
        algorithm_id="multimodal_mamba_fusion",
        required_inputs=("embeddings",),
        optional_inputs=("tracks",),
        output_keys=("fused_embeddings",),
        run_unwrap_key=None,
        description="Multimodal Mamba 时序融合",
    ),
    "supcon_meta_classifier": AlgorithmContract(
        algorithm_id="supcon_meta_classifier",
        required_inputs=("fused_embeddings",),
        optional_inputs=("support_shots",),
        output_keys=("classifications",),
        run_unwrap_key="classifications",
        description="SupCon + Meta 隶属/威胁分类",
        input_aliases={"embeddings": "fused_embeddings"},
    ),
    "synapse_rag_retriever": AlgorithmContract(
        algorithm_id="synapse_rag_retriever",
        required_inputs=("classifications",),
        optional_inputs=("knowledge_base", "query"),
        output_keys=("entities", "rag_context"),
        run_unwrap_key=None,
        description="SynapseRAG 实体检索",
    ),
    "knowledge_semantic_comm": AlgorithmContract(
        algorithm_id="knowledge_semantic_comm",
        required_inputs=("perception", "cognition"),
        optional_inputs=(),
        output_keys=("summary", "targets", "compression_ratio"),
        run_unwrap_key=None,
        description="知识驱动语义压缩",
        input_aliases={
            "perception_output": "perception",
            "cognition_output": "cognition",
        },
    ),
    "anti_jam_mcdm_router": AlgorithmContract(
        algorithm_id="anti_jam_mcdm_router",
        required_inputs=("packet",),
        optional_inputs=("subscriber_agents", "jamming_level"),
        output_keys=("routes",),
        run_unwrap_key=None,
        description="抗干扰 MCDM/SAW 路由（可选 PPO；默认 SAW）",
        input_aliases={"compressed": "packet", "semantic_packet": "packet"},
    ),
}


def get_contract(algorithm_id: str) -> AlgorithmContract:
    try:
        return CONTRACTS[algorithm_id]
    except KeyError as exc:
        raise KeyError(f"unknown algorithm contract: {algorithm_id}") from exc


def contract_summary() -> list[dict[str, Any]]:
    return [
        {
            "algorithm_id": c.algorithm_id,
            "required_inputs": list(c.required_inputs),
            "optional_inputs": list(c.optional_inputs),
            "output_keys": list(c.output_keys),
            "description": c.description,
        }
        for c in CONTRACTS.values()
    ]
