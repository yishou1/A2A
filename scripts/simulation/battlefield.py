"""
统一战场模拟：仅包含当前流水线实际使用的模态与上下文。

阶段（按战术时间线）:
  1. recon    — 侦察：双路视觉（EO + SAR），建立检测与 CLIP 嵌入
  2. contact  — 接触：视觉 + 前沿文本报告 + 毁伤参考帧
  3. bda      — 打击后评估：相对参考帧的毁伤变化（OpenCV 帧差）
  4. jammed   — 强干扰：验证抗干扰路由与语义压缩
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from agent.models.schemas import SensorBatch, SensorFrame, SensorModality

from scripts.simulation.images import (
    encode_image_b64,
    load_base_scene_rgb,
    make_damaged_scene_rgb,
    resize_rgb,
)
from scripts.simulation.situation import DEFAULT_SCENARIO, RedBlueSituation


class SimPhase(str, Enum):
    RECON = "recon"
    CONTACT = "contact"
    BDA = "bda"
    JAMMED = "jammed"


@dataclass
class SimulationConfig:
    mission_id: str = "OP-IRON-VALLEY-2026"
    base_lat: float = 30.512
    base_lon: float = 114.381
    subscriber_agents: list[str] = field(
        default_factory=lambda: [
            "command_agent",
            "fire_control_agent",
            "recon_fusion_agent",
        ]
    )


PHASES: list[tuple[SimPhase, str, str]] = [
    (SimPhase.RECON, "01_recon", "侦察建立态势"),
    (SimPhase.CONTACT, "02_contact", "接触与目标关联"),
    (SimPhase.BDA, "03_bda", "打击后毁伤评估"),
    (SimPhase.JAMMED, "04_jammed", "强电磁干扰下通信"),
]

PHASE_TO_SITUATION_KEY = {
    SimPhase.RECON: "recon",
    SimPhase.CONTACT: "contact",
    SimPhase.BDA: "bda",
    SimPhase.JAMMED: "jammed",
}


class BattlefieldSimulation:
    """按阶段生成 SensorBatch，图像内容适配 YOLOv8 / OpenCV / CLIP。"""

    def __init__(
        self,
        config: SimulationConfig | None = None,
        *,
        situation: RedBlueSituation | None = None,
        scenario_path: str | Path | None = None,
    ):
        self.config = config or SimulationConfig()
        self.situation = situation or RedBlueSituation(scenario_path or DEFAULT_SCENARIO)
        if self.config.mission_id == "OP-IRON-VALLEY-2026":
            self.config.mission_id = self.situation.mission_id
        base = load_base_scene_rgb()
        self._scene_clear = resize_rgb(base)
        self._scene_damaged = resize_rgb(make_damaged_scene_rgb(base, severity=0.45))
        self._b64_clear = encode_image_b64(self._scene_clear)
        self._b64_damaged = encode_image_b64(self._scene_damaged)

    def _ts(self, offset_sec: int = 0) -> datetime:
        return datetime.now(timezone.utc) + timedelta(seconds=offset_sec)

    def _eo(
        self,
        sensor_id: str,
        *,
        image_b64: str,
        scene: str,
        offset_sec: int = 0,
    ) -> SensorFrame:
        return SensorFrame(
            sensor_id=sensor_id,
            modality=SensorModality.EO_IR,
            timestamp=self._ts(offset_sec),
            payload={
                "image_base64": image_b64,
                "scene_tag": scene,
                "fov_deg": 45.0,
            },
            metadata={
                "resolution": f"{self._scene_clear.shape[1]}x{self._scene_clear.shape[0]}",
                "platform": "UAV-Reaper-07",
                "altitude_m": 3200.0,
            },
        )

    def _sar(self, sensor_id: str, *, image_b64: str, offset_sec: int = 0) -> SensorFrame:
        return SensorFrame(
            sensor_id=sensor_id,
            modality=SensorModality.SAR,
            timestamp=self._ts(offset_sec),
            payload={
                "image_base64": image_b64,
                "polarization": "HH",
                "grazing_angle_deg": 32.5,
            },
            metadata={
                "resolution": f"{self._scene_clear.shape[1]}x{self._scene_clear.shape[0]}",
                "platform": "SAT-SAR-02",
            },
        )

    def _text_report(self, sensor_id: str, text: str, *, offset_sec: int = 0) -> SensorFrame:
        return SensorFrame(
            sensor_id=sensor_id,
            modality=SensorModality.TEXT_REPORT,
            timestamp=self._ts(offset_sec),
            payload={"report_text": text, "source": "forward_observer"},
            metadata={"classification": "SECRET//NOFORN"},
        )

    def _reference(self, *, image_b64: str, scene: str) -> dict[str, Any]:
        return {
            "sensor_id": "EO-REF-0",
            "modality": "eo_ir",
            "timestamp": self._ts(-120).isoformat(),
            "payload": {"image_base64": image_b64, "scene_tag": scene},
            "metadata": {"role": "reference_for_damage_assessment"},
        }

    def _context(
        self,
        *,
        phase: SimPhase,
        reference: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sit_key = PHASE_TO_SITUATION_KEY[phase]
        overlay = self.situation.context_overlay(sit_key)
        snap = overlay["battlefield_situation"]
        cfg = self.config
        ao = snap.get("area_of_operations") or {}
        ctx: dict[str, Any] = {
            "operation": cfg.mission_id,
            "operation_name": self.situation.operation_name,
            "phase": phase.value,
            "phase_label": snap.get("phase_label"),
            "jamming_level": overlay["jamming_level"],
            "subscriber_agents": overlay["subscriber_agents"],
            "area_of_operations": ao,
            "knowledge_base": self.situation.knowledge_base(),
            "rag_query": overlay["rag_query"],
            "battlefield_situation": snap,
            "friendly_side": overlay["friendly_side"],
            "enemy_side": overlay["enemy_side"],
            "simulation_force_prior": overlay.get("simulation_force_prior"),
        }
        if reference:
            ctx["reference_frame"] = reference
        return ctx

    def batch_recon(self) -> SensorBatch:
        cfg = self.config
        return SensorBatch(
            mission_id=cfg.mission_id,
            frames=[
                self._eo("EO-FWD-1", image_b64=self._b64_clear, scene="recon_corridor", offset_sec=0),
                self._sar("SAR-1", image_b64=self._b64_clear, offset_sec=1),
            ],
            context=self._context(phase=SimPhase.RECON),
        )

    def batch_contact(self) -> SensorBatch:
        cfg = self.config
        return SensorBatch(
            mission_id=cfg.mission_id,
            frames=[
                self._eo(
                    "EO-FWD-1",
                    image_b64=self._b64_clear,
                    scene="contact_primary",
                    offset_sec=0,
                ),
                self._sar("SAR-1", image_b64=self._b64_clear, offset_sec=1),
                self._text_report(
                    "FO-1",
                    self.situation.snapshot_for_phase("contact")["observer_report"],
                    offset_sec=2,
                ),
            ],
            context=self._context(
                phase=SimPhase.CONTACT,
                reference=self._reference(image_b64=self._b64_clear, scene="pre_contact_baseline"),
            ),
        )

    def batch_bda(self) -> SensorBatch:
        cfg = self.config
        return SensorBatch(
            mission_id=cfg.mission_id,
            frames=[
                self._eo(
                    "EO-FWD-1",
                    image_b64=self._b64_damaged,
                    scene="post_strike_bda",
                    offset_sec=0,
                ),
                self._sar("SAR-1", image_b64=self._b64_damaged, offset_sec=1),
            ],
            context=self._context(
                phase=SimPhase.BDA,
                reference=self._reference(image_b64=self._b64_clear, scene="pre_strike"),
            ),
        )

    def batch_jammed(self) -> SensorBatch:
        cfg = self.config
        return SensorBatch(
            mission_id=cfg.mission_id,
            frames=[
                self._eo(
                    "EO-FWD-1",
                    image_b64=self._b64_clear,
                    scene="jammed_corridor",
                    offset_sec=0,
                ),
                self._text_report(
                    "FO-1",
                    self.situation.snapshot_for_phase("jammed")["observer_report"],
                    offset_sec=1,
                ),
            ],
            context=self._context(phase=SimPhase.JAMMED),
        )

    def stream_tracking(self, *, batches: int = 3) -> Iterator[SensorBatch]:
        """同一任务连续批次，用于 IoU 跟踪延续（复用同一视觉场景）。"""
        cfg = self.config
        for i in range(batches):
            yield SensorBatch(
                mission_id=cfg.mission_id,
                frames=[
                    self._eo(
                        "EO-FWD-1",
                        image_b64=self._b64_clear,
                        scene=f"track_t{i}",
                        offset_sec=i * 8,
                    ),
                ],
                context=self._context(
                    phase=SimPhase.CONTACT,
                    reference=self._reference(image_b64=self._b64_clear, scene="pre_contact_baseline")
                    if i == 0
                    else None,
                ),
            )

    def all_phases(self) -> list[tuple[SimPhase, str, str, SensorBatch]]:
        builders = {
            SimPhase.RECON: self.batch_recon,
            SimPhase.CONTACT: self.batch_contact,
            SimPhase.BDA: self.batch_bda,
            SimPhase.JAMMED: self.batch_jammed,
        }
        out: list[tuple[SimPhase, str, str, SensorBatch]] = []
        for phase, prefix, label in PHASES:
            out.append((phase, prefix, label, builders[phase]()))
        return out
