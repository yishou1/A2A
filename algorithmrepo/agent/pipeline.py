"""传感器批次接入、三技能处理、情报包导出（供下游 Agent）。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from agent.config_profiles import apply_compute_profile
from agent.models.schemas import SemanticIntelligencePacket, SensorBatch, SensorModality
from agent.orchestrator import TacticalIntelligenceAgent

VISUAL_MODALITIES = {SensorModality.EO_IR, SensorModality.SAR}


def load_config() -> dict[str, Any]:
    path = os.environ.get("TIA_CONFIG", "config/default.yaml")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {}
    return apply_compute_profile(cfg)


def agent_config_from_yaml(cfg: dict[str, Any]) -> dict[str, Any]:
    """将 default.yaml 转为编排器所需的 config 结构。"""
    skills = cfg.get("skills") or {}
    return {
        "perception": skills.get("perception"),
        "cognition": skills.get("cognition"),
        "communication": skills.get("communication"),
        "inference": cfg.get("inference") or {},
        "artifact_storage": cfg.get("artifact_storage") or {},
    }


def create_agent(config: dict[str, Any] | None = None) -> TacticalIntelligenceAgent:
    cfg = config if config is not None else load_config()
    return TacticalIntelligenceAgent(
        use_mock=cfg.get("use_mock", True),
        config=agent_config_from_yaml(cfg),
    )


def load_sensor_batch(path: str | Path) -> SensorBatch:
    """从 JSON 文件加载 SensorBatch。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return SensorBatch.model_validate(data)


def skill_alignment_report(batch: SensorBatch) -> dict[str, Any]:
    """
    说明本批次数据将如何进入各技能（便于核对模态与上下文是否齐全）。
    """
    perception_visual: list[str] = []
    cognition_all: list[str] = []
    other: list[str] = []

    for frame in batch.frames:
        sid = frame.sensor_id
        cognition_all.append(f"{sid} ({frame.modality.value})")
        if frame.modality in VISUAL_MODALITIES:
            perception_visual.append(f"{sid} ({frame.modality.value})")
        else:
            other.append(f"{sid} ({frame.modality.value})")

    ctx = batch.context
    return {
        "mission_id": batch.mission_id,
        "frame_count": len(batch.frames),
        "skill_1_perception": {
            "algorithms": [
                "RT-DETR+ODConv",
                "Siamese-Mask2Former",
                "EDL-Evidential-Deep-Learning",
                "MOTR+Neural-Kalman",
            ],
            "visual_frames_for_detection": perception_visual,
            "note": "仅 eo_ir / sar 进入 RT-DETR 检测与 Siamese Mask2Former 毁伤",
        },
        "skill_2_cognition": {
            "algorithms": [
                "ImageBind-CrossModal",
                "Multimodal-Mamba",
                "SupCon+Meta-Learning",
                "SynapseRAG",
            ],
            "all_frames_for_embedding": cognition_all,
            "has_knowledge_base": bool(ctx.get("knowledge_base")),
            "rag_query": ctx.get("rag_query", "(默认)"),
        },
        "skill_3_communication": {
            "algorithms": ["Knowledge-Semantic-Comm", "MARL-Dynamic-Routing"],
            "subscriber_agents": ctx.get("subscriber_agents") or [],
            "jamming_level": float(ctx.get("jamming_level", 0.0)),
        },
        "non_visual_modalities": other,
        "has_reference_frame": bool(ctx.get("reference_frame")),
    }


def process_and_package(
    batch: SensorBatch,
    *,
    agent: TacticalIntelligenceAgent | None = None,
) -> SemanticIntelligencePacket:
    """执行完整流水线，返回供下游 Agent 消费的 SemanticIntelligencePacket。"""
    if not batch.frames:
        raise ValueError("frames 不能为空")
    runner = agent or create_agent()
    return runner.process(batch)


def export_packet(
    packet: SemanticIntelligencePacket,
    output_dir: str | Path,
    *,
    mission_subdir: bool = True,
) -> Path:
    """将情报包写入 JSON 文件，返回写入路径。"""
    base = Path(output_dir)
    if mission_subdir:
        base = base / packet.mission_id.replace("/", "_")
    base.mkdir(parents=True, exist_ok=True)

    out_path = base / f"{packet.packet_id}.json"
    out_path.write_text(
        json.dumps(packet.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    latest = base / "latest.json"
    latest.write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")
    return out_path
