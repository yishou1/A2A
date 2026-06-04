"""
红蓝战场态势构建：从 YAML 场景加载编制、位置、阶段叙述，并注入 SensorBatch.context。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

DEFAULT_SCENARIO = Path(__file__).resolve().parent / "scenarios" / "iron_valley_red_blue.yaml"


@dataclass
class ForceUnit:
    unit_id: str
    name: str
    type: str
    geo: dict[str, float]
    status: str = "active"
    strength: int | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "unit_id": self.unit_id,
            "name": self.name,
            "type": self.type,
            "geo": self.geo,
            "status": self.status,
            "notes": self.notes,
        }
        if self.strength is not None:
            d["strength"] = self.strength
        return d


@dataclass
class ForceSide:
    """红方或蓝方编制。"""

    side: str  # "blue" | "red"
    designation: str
    commander: str
    units: list[ForceUnit] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "side_label": "蓝方（我方）" if self.side == "blue" else "红方（敌方）",
            "designation": self.designation,
            "commander": self.commander,
            "unit_count": len(self.units),
            "units": [u.to_dict() for u in self.units],
        }


@dataclass
class PhaseSituation:
    phase_key: str
    label: str
    jamming_level: float
    blue_summary: str
    red_summary: str
    observer_report: str
    rag_query: str
    red_unit_updates: list[dict[str, Any]] = field(default_factory=list)


class RedBlueSituation:
    """可编辑的红蓝对抗态势目录。"""

    def __init__(self, scenario_path: str | Path | None = None):
        path = Path(scenario_path or DEFAULT_SCENARIO)
        if not path.is_file():
            raise FileNotFoundError(f"场景文件不存在: {path}")
        with open(path, encoding="utf-8") as f:
            self._raw = yaml.safe_load(f) or {}
        self.scenario_path = str(path.resolve())
        self._blue = self._parse_side("blue_force", "blue")
        self._red = self._parse_side("red_force", "red")
        self._phases = self._parse_phases()

    @property
    def mission_id(self) -> str:
        return str(self._raw.get("mission_id", "OP-UNKNOWN"))

    @property
    def operation_name(self) -> str:
        return str(self._raw.get("operation_name", ""))

    def _parse_side(self, key: str, side: str) -> ForceSide:
        block = self._raw.get(key) or {}
        units: list[ForceUnit] = []
        for u in block.get("units") or []:
            units.append(
                ForceUnit(
                    unit_id=str(u["unit_id"]),
                    name=str(u.get("name", u["unit_id"])),
                    type=str(u.get("type", "unknown")),
                    geo=dict(u.get("geo") or {}),
                    status=str(u.get("status", "active")),
                    strength=u.get("strength"),
                    notes=str(u.get("notes", "")),
                )
            )
        return ForceSide(
            side=side,
            designation=str(block.get("designation", side)),
            commander=str(block.get("commander", "")),
            units=units,
        )

    def _parse_phases(self) -> dict[str, PhaseSituation]:
        phases: dict[str, PhaseSituation] = {}
        for key, block in (self._raw.get("phases") or {}).items():
            phases[key] = PhaseSituation(
                phase_key=key,
                label=str(block.get("label", key)),
                jamming_level=float(block.get("jamming_level", 0.0)),
                blue_summary=str(block.get("blue_summary", "")),
                red_summary=str(block.get("red_summary", "")),
                observer_report=str(block.get("observer_report", "")).strip(),
                rag_query=str(block.get("rag_query", "战场态势与威胁规则")),
                red_unit_updates=list(block.get("red_unit_updates") or []),
            )
        return phases

    def _red_force_at_phase(self, phase_key: str) -> ForceSide:
        red = copy.deepcopy(self._red)
        for key in self._phases:
            self._apply_red_updates(red, self._phases[key].red_unit_updates)
            if key == phase_key:
                break
        return red

    @staticmethod
    def _apply_red_updates(force: ForceSide, updates: list[dict[str, Any]]) -> None:
        by_id = {u.unit_id: u for u in force.units}
        for upd in updates:
            uid = upd.get("unit_id")
            if uid not in by_id:
                continue
            unit = by_id[uid]
            if "status" in upd:
                unit.status = str(upd["status"])
            if "strength" in upd:
                unit.strength = int(upd["strength"])
            if "notes" in upd:
                unit.notes = str(upd["notes"])

    def snapshot_for_phase(self, phase_key: str) -> dict[str, Any]:
        """返回某战术阶段的完整红蓝态势快照（含阶段叙述）。"""
        if phase_key not in self._phases:
            raise KeyError(f"未知阶段: {phase_key}，可选: {list(self._phases)}")

        red_at_phase = self._red_force_at_phase(phase_key)
        phase = self._phases[phase_key]
        ao = self._raw.get("area_of_operations") or {}

        return {
            "schema": "battlefield_situation/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scenario_file": self.scenario_path,
            "mission_id": self.mission_id,
            "operation_name": self.operation_name,
            "phase": phase_key,
            "phase_label": phase.label,
            "friendly_side": self._raw.get("friendly_side", "blue"),
            "enemy_side": self._raw.get("enemy_side", "red"),
            "area_of_operations": ao,
            "blue_force": self._blue.to_dict(),
            "red_force": red_at_phase.to_dict(),
            "phase_narrative": {
                "blue_summary": phase.blue_summary,
                "red_summary": phase.red_summary,
                "combined_assessment": (
                    f"{phase.blue_summary} | {phase.red_summary}"
                ),
            },
            "observer_report": phase.observer_report,
            "jamming_level": phase.jamming_level,
            "rag_query": phase.rag_query,
            "control_measures": ao.get("control_measures", []),
        }

    def all_phase_snapshots(self) -> dict[str, dict[str, Any]]:
        return {key: self.snapshot_for_phase(key) for key in self._phases}

    def master_overview(self) -> dict[str, Any]:
        """战役级红蓝总览（不区分阶段）。"""
        return {
            "schema": "battlefield_situation_overview/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scenario_file": self.scenario_path,
            "mission_id": self.mission_id,
            "operation_name": self.operation_name,
            "area_of_operations": self._raw.get("area_of_operations") or {},
            "blue_force": self._blue.to_dict(),
            "red_force": self._red.to_dict(),
            "phases": [
                {
                    "phase": k,
                    "label": p.label,
                    "jamming_level": p.jamming_level,
                }
                for k, p in self._phases.items()
            ],
        }

    def context_overlay(self, phase_key: str) -> dict[str, Any]:
        """注入 SensorBatch.context 的字段。"""
        snap = self.snapshot_for_phase(phase_key)
        return {
            "battlefield_situation": snap,
            "friendly_side": snap["friendly_side"],
            "enemy_side": snap["enemy_side"],
            "jamming_level": snap["jamming_level"],
            "rag_query": snap["rag_query"],
            "subscriber_agents": self._raw.get("subscriber_agents")
            or [
                "command_agent",
                "fire_control_agent",
                "recon_fusion_agent",
            ],
            "simulation_force_prior": {
                "prefer_hostile_for_first_n_tracks": 4,
                "rationale": "模拟场主要威胁来自红方装甲/车辆目标",
            },
        }

    def knowledge_base(self) -> list[dict[str, Any]]:
        return [
            {
                "page": "ROE-BLUE-2026",
                "text": "蓝方部队：识别为红方（敌方）装甲/车辆目标进入禁戒区须上报并建议交战。",
            },
            {
                "page": "SOP-RED-THREAT",
                "text": "红方装甲纵队、机动防空单元为高威胁实体；毁伤评估>0.6 时通知火控 Agent。",
            },
            {
                "page": "IFF-IRON-VALLEY",
                "text": "铁谷方向：北侧为红方主要接近路，蓝方侦察力量部署于南侧高地。",
            },
        ]
