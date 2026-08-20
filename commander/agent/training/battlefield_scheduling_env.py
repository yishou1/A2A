"""
战场任务调度多智能体环境（MARL）。

智能体：传感器（EO/SAR/雷达）+ 打击资产（火炮/导弹单元）
任务：目标覆盖分配、毁伤不足目标的重攻击规划

状态编码贴近真实战场态势：威胁等级、毁伤评估、地理距离、干扰强度、传感器模态匹配。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

MAX_TARGETS = 8
MAX_SENSORS = 4
MAX_STRIKE_ASSETS = 4
TARGET_FEAT_DIM = 7
SENSOR_FEAT_DIM = 6
STRIKE_FEAT_DIM = 4
CONTEXT_FEAT_DIM = 4

MODALITY_INDEX = {"eo_ir": 0, "sar": 1, "radar": 2, "acoustic": 3}


@dataclass
class SchedulingTarget:
    target_id: str
    threat_score: float
    damage_score: float
    confidence: float
    lat: float
    lon: float
    needs_reattack: bool = False
    class_name: str = "unknown"


@dataclass
class SchedulingSensor:
    sensor_id: str
    modality: str
    available: bool = True
    load: float = 0.0
    lat: float = 0.0
    lon: float = 0.0


@dataclass
class StrikeAsset:
    asset_id: str
    asset_type: str
    available: bool = True
    remaining_ammo: float = 1.0


@dataclass
class BattlefieldSchedulingState:
    targets: list[SchedulingTarget] = field(default_factory=list)
    sensors: list[SchedulingSensor] = field(default_factory=list)
    strike_assets: list[StrikeAsset] = field(default_factory=list)
    jamming_level: float = 0.0
    phase: str = "recon"
    base_lat: float = 30.512
    base_lon: float = 114.381


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def _modality_match(sensor_modality: str, target_class: str) -> float:
    """模态-目标类型匹配度（战场先验）。"""
    cls = target_class.lower()
    mod = sensor_modality.lower()
    if mod == "eo_ir":
        return 1.0 if cls in ("tank", "person", "vehicle", "artillery", "building") else 0.7
    if mod == "sar":
        return 1.0 if cls in ("ship", "building", "vehicle", "tank") else 0.75
    if mod == "radar":
        return 1.0 if cls in ("airplane", "helicopter", "drone", "ship") else 0.6
    return 0.5


class BattlefieldSchedulingEnv:
    """
    参数共享 MARL 环境：每个传感器/打击资产为一个智能体。

    动作空间：选择目标索引（0=不分配，1..N=目标）
    奖励：威胁覆盖、模态匹配、重攻击需求满足、资源效率
    """

    def __init__(self, max_targets: int = MAX_TARGETS, max_sensors: int = MAX_SENSORS):
        self.max_targets = max_targets
        self.max_sensors = max_sensors
        self.max_strike = MAX_STRIKE_ASSETS
        self.n_agents = max_sensors + MAX_STRIKE_ASSETS
        self.n_actions = max_targets + 1  # +1 for no-assignment
        self.state: BattlefieldSchedulingState | None = None
        self._sensor_assignments: dict[str, str | None] = {}
        self._strike_assignments: dict[str, str | None] = {}

    @property
    def obs_dim(self) -> int:
        return (
            self.max_targets * TARGET_FEAT_DIM
            + self.max_sensors * SENSOR_FEAT_DIM
            + MAX_STRIKE_ASSETS * STRIKE_FEAT_DIM
            + CONTEXT_FEAT_DIM
            + 2  # agent role one-hot (sensor vs strike)
        )

    def reset(self, situation: BattlefieldSchedulingState) -> np.ndarray:
        self.state = situation
        self._sensor_assignments = {s.sensor_id: None for s in situation.sensors[: self.max_sensors]}
        self._strike_assignments = {a.asset_id: None for a in situation.strike_assets[: self.max_strike]}
        return self._build_global_obs()

    def step(self, actions: list[int]) -> tuple[np.ndarray, list[float], bool, dict[str, Any]]:
        assert self.state is not None
        rewards: list[float] = []
        sensors = self.state.sensors[: self.max_sensors]
        strikes = self.state.strike_assets[: self.max_strike]
        targets = self.state.targets[: self.max_targets]

        covered: set[str] = set()
        for i, sensor in enumerate(sensors):
            act = actions[i] if i < len(actions) else 0
            target_id = None
            if 0 < act <= len(targets):
                target_id = targets[act - 1].target_id
                covered.add(target_id)
            self._sensor_assignments[sensor.sensor_id] = target_id
            rewards.append(self._sensor_reward(sensor, target_id, targets, act))

        reattacked: set[str] = set()
        for j, asset in enumerate(strikes):
            idx = self.max_sensors + j
            act = actions[idx] if idx < len(actions) else 0
            target_id = None
            if 0 < act <= len(targets):
                target_id = targets[act - 1].target_id
                reattacked.add(target_id)
            self._strike_assignments[asset.asset_id] = target_id
            rewards.append(self._strike_reward(asset, target_id, targets, act))

        global_reward = self._global_reward(covered, reattacked, targets)
        rewards = [r + global_reward / max(self.n_agents, 1) for r in rewards]

        info = {
            "sensor_assignments": dict(self._sensor_assignments),
            "strike_assignments": dict(self._strike_assignments),
            "covered_targets": list(covered),
            "reattack_targets": list(reattacked),
        }
        return self._build_global_obs(), rewards, True, info

    def _sensor_reward(
        self,
        sensor: SchedulingSensor,
        target_id: str | None,
        targets: list[SchedulingTarget],
        action: int,
    ) -> float:
        if not sensor.available:
            return -0.1 if action > 0 else 0.0
        if target_id is None:
            high_threat_uncovered = any(t.threat_score > 0.7 for t in targets)
            return -0.3 if high_threat_uncovered else 0.05
        target = next((t for t in targets if t.target_id == target_id), None)
        if target is None:
            return -0.5
        dist = _haversine_km(sensor.lat, sensor.lon, target.lat, target.lon)
        dist_score = max(0.0, 1.0 - dist / 50.0)
        match = _modality_match(sensor.modality, target.class_name)
        jam_penalty = self.state.jamming_level * 0.3 if sensor.modality == "eo_ir" else self.state.jamming_level * 0.1
        threat_bonus = target.threat_score * 0.5
        return dist_score * 0.3 + match * 0.3 + threat_bonus - jam_penalty - sensor.load * 0.2

    def _strike_reward(
        self,
        asset: StrikeAsset,
        target_id: str | None,
        targets: list[SchedulingTarget],
        action: int,
    ) -> float:
        if not asset.available or asset.remaining_ammo <= 0:
            return -0.2 if action > 0 else 0.0
        if target_id is None:
            needs = [t for t in targets if t.needs_reattack]
            return -0.4 if needs else 0.1
        target = next((t for t in targets if t.target_id == target_id), None)
        if target is None:
            return -0.5
        if target.needs_reattack:
            return 0.8 + target.threat_score * 0.3 - (1.0 - target.damage_score) * 0.2
        if target.damage_score > 0.7:
            return -0.3
        return 0.1 + target.threat_score * 0.2

    def _global_reward(
        self,
        covered: set[str],
        reattacked: set[str],
        targets: list[SchedulingTarget],
    ) -> float:
        if not targets:
            return 0.0
        high_threat = [t for t in targets if t.threat_score >= 0.6]
        coverage = sum(1 for t in high_threat if t.target_id in covered) / max(len(high_threat), 1)
        reattack_need = [t for t in targets if t.needs_reattack]
        reattack_rate = sum(1 for t in reattack_need if t.target_id in reattacked) / max(len(reattack_need), 1)
        duplicate_penalty = max(0, len(covered) - len(set(covered))) * 0.1
        return coverage * 0.6 + reattack_rate * 0.8 - duplicate_penalty

    def _build_global_obs(self) -> np.ndarray:
        assert self.state is not None
        feats: list[float] = []
        targets = self.state.targets[: self.max_targets]
        sensors = self.state.sensors[: self.max_sensors]
        strikes = self.state.strike_assets[: self.max_strike]

        for i in range(self.max_targets):
            if i < len(targets):
                t = targets[i]
                feats.extend(
                    [
                        t.threat_score,
                        t.damage_score,
                        t.confidence,
                        (t.lat - self.state.base_lat) * 100,
                        (t.lon - self.state.base_lon) * 100,
                        float(t.needs_reattack),
                        float(t.target_id in set(self._sensor_assignments.values())),
                    ]
                )
            else:
                feats.extend([0.0] * TARGET_FEAT_DIM)

        for i in range(self.max_sensors):
            if i < len(sensors):
                s = sensors[i]
                mod_idx = MODALITY_INDEX.get(s.modality, 3)
                mod_onehot = [0.0, 0.0, 0.0, 0.0]
                if mod_idx < 4:
                    mod_onehot[mod_idx] = 1.0
                feats.extend(
                    mod_onehot[:3]
                    + [float(s.available), s.load, self.state.jamming_level * (1.0 if s.modality == "eo_ir" else 0.3)]
                )
            else:
                feats.extend([0.0] * SENSOR_FEAT_DIM)

        for i in range(self.max_strike):
            if i < len(strikes):
                a = strikes[i]
                feats.extend(
                    [
                        float(a.available),
                        a.remaining_ammo,
                        float(a.asset_type in ("artillery", "mlrs")),
                        float(a.asset_type in ("atgm", "missile")),
                    ]
                )
            else:
                feats.extend([0.0] * STRIKE_FEAT_DIM)

        phase_enc = {"recon": 0.0, "contact": 0.33, "bda": 0.66, "jammed": 1.0}.get(self.state.phase, 0.5)
        feats.extend(
            [
                self.state.jamming_level,
                phase_enc,
                len(targets) / self.max_targets,
                len([t for t in targets if t.needs_reattack]) / max(len(targets), 1),
            ]
        )
        feats.extend([1.0, 0.0])  # placeholder agent role; per-agent obs built in training
        return np.array(feats, dtype=np.float32)

    def build_agent_obs(self, agent_idx: int) -> np.ndarray:
        """为指定智能体构建观测（全局态势 + 智能体角色标识）。"""
        obs = self._build_global_obs()
        obs[-2:] = [1.0, 0.0] if agent_idx < self.max_sensors else [0.0, 1.0]
        return obs


def situation_from_perception(
    tracks: list[dict[str, Any]],
    detections: list[dict[str, Any]],
    batch_context: dict[str, Any],
    frames: list[dict[str, Any]],
) -> BattlefieldSchedulingState:
    """从感知输出与批次上下文构建调度态势。"""
    ctx = batch_context or {}
    bf = ctx.get("battlefield_situation") or {}
    enemy = (bf.get("red_force") or {}).get("units") or []
    friendly = (bf.get("blue_force") or {}).get("units") or []
    base_lat = float(ctx.get("base_lat", ctx.get("ground_elevation_m", 30.512)))
    base_lon = float(ctx.get("base_lon", 114.381))
    if isinstance(ctx.get("area_of_operations"), dict):
        ao = ctx["area_of_operations"].get("center") or {}
        base_lat = float(ao.get("lat", base_lat))
        base_lon = float(ao.get("lon", base_lon))

    det_by_track = {d.get("track_id"): d for d in detections if d.get("track_id")}
    targets: list[SchedulingTarget] = []
    for tr in tracks[:MAX_TARGETS]:
        tid = str(tr.get("track_id", f"T-{len(targets)}"))
        det = det_by_track.get(tid, {})
        geo = tr.get("geo") or {}
        if isinstance(geo, dict):
            lat = float(geo.get("lat", base_lat))
            lon = float(geo.get("lon", base_lon))
        else:
            lat, lon = base_lat, base_lon
        damage = float(det.get("damage_score", tr.get("damage_score", 0.0)) or 0.0)
        conf = float(det.get("confidence", tr.get("confidence", 0.5)) or 0.5)
        threat = min(1.0, conf * (1.0 - damage * 0.3))
        class_name = str(det.get("class_name", tr.get("class_name", "unknown")))
        needs_reattack = damage < 0.55 and threat > 0.4
        if ctx.get("phase") == "bda":
            needs_reattack = damage < 0.65 and threat > 0.35
        targets.append(
            SchedulingTarget(
                target_id=tid,
                threat_score=threat,
                damage_score=damage,
                confidence=conf,
                lat=lat,
                lon=lon,
                needs_reattack=needs_reattack,
                class_name=class_name,
            )
        )

    if not targets and enemy:
        for u in enemy[:MAX_TARGETS]:
            geo = u.get("geo") or {}
            targets.append(
                SchedulingTarget(
                    target_id=str(u.get("unit_id", f"E-{len(targets)}")),
                    threat_score=0.7 if u.get("status") == "active" else 0.3,
                    damage_score=0.0,
                    confidence=0.6,
                    lat=float(geo.get("lat", base_lat)),
                    lon=float(geo.get("lon", base_lon)),
                    needs_reattack=False,
                    class_name=str(u.get("type", "unknown")),
                )
            )

    sensors: list[SchedulingSensor] = []
    for fr in frames[:MAX_SENSORS]:
        meta = fr.get("metadata") or {}
        sensors.append(
            SchedulingSensor(
                sensor_id=str(fr.get("sensor_id", f"S-{len(sensors)}")),
                modality=str(fr.get("modality", "eo_ir")),
                available=True,
                load=0.0,
                lat=float(meta.get("platform_lat", base_lat)),
                lon=float(meta.get("platform_lon", base_lon)),
            )
        )
    if not sensors:
        sensors.append(
            SchedulingSensor(sensor_id="EO-FWD-1", modality="eo_ir", lat=base_lat, lon=base_lon)
        )

    strike_assets: list[StrikeAsset] = []
    for u in friendly:
        utype = str(u.get("type", ""))
        if utype in ("artillery", "mlrs", "atgm", "missile", "fire_support"):
            strike_assets.append(
                StrikeAsset(
                    asset_id=str(u.get("unit_id", f"A-{len(strike_assets)}")),
                    asset_type=utype,
                    available=u.get("status", "active") == "active",
                    remaining_ammo=float(u.get("strength", 100)) / 100.0,
                )
            )
    if not strike_assets:
        strike_assets = [
            StrikeAsset(asset_id="ARTY-1", asset_type="artillery"),
            StrikeAsset(asset_id="ATGM-1", asset_type="atgm"),
        ]

    return BattlefieldSchedulingState(
        targets=targets,
        sensors=sensors,
        strike_assets=strike_assets[:MAX_STRIKE_ASSETS],
        jamming_level=float(ctx.get("jamming_level", 0.0)),
        phase=str(ctx.get("phase", "recon")),
        base_lat=base_lat,
        base_lon=base_lon,
    )
