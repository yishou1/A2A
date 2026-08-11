"""AMOS JSON → BattlefieldSchedulingState（不改 obs_dim，映射进现有字段）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent.training.battlefield_scheduling_env import (
    MAX_SENSORS,
    MAX_STRIKE_ASSETS,
    MAX_TARGETS,
    BattlefieldSchedulingState,
    SchedulingSensor,
    SchedulingTarget,
    StrikeAsset,
)

# 电量 / 链路低于阈值时强制不可用
BATTERY_UNAVAILABLE = 0.12
LINK_UNAVAILABLE = 0.25


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _window_urgency(
    now: datetime | None,
    window: dict[str, Any] | None,
) -> tuple[float, bool]:
    """返回 (urgency_boost [0,1], in_window)。

    - 窗外：urgency=0, in_window=False（任务降权）
    - 窗内：越接近 end 越接近 1
    - 无窗口：视为在窗内，urgency=0
    """
    if not window:
        return 0.0, True
    start = _parse_dt(window.get("start"))
    end = _parse_dt(window.get("end"))
    if now is None:
        return 0.0, True
    if start and now < start:
        return 0.0, False
    if end and now > end:
        return 0.0, False
    if not end:
        return 0.15, True
    # 剩余时间占比：越少越紧迫
    if start and end and end > start:
        total = (end - start).total_seconds()
        remain = max(0.0, (end - now).total_seconds())
        if total <= 0:
            return 0.5, True
        urgency = 1.0 - (remain / total)
        return _clamp01(urgency), True
    return 0.2, True


def _mean_link_quality(payload: dict[str, Any], platforms: list[dict[str, Any]]) -> float:
    qualities: list[float] = []
    for link in payload.get("links") or []:
        if isinstance(link, dict) and link.get("quality") is not None:
            qualities.append(_clamp01(float(link["quality"])))
    for p in platforms:
        if p.get("link_quality") is not None:
            qualities.append(_clamp01(float(p["link_quality"])))
    if not qualities:
        return 1.0
    return sum(qualities) / len(qualities)


def situation_from_amos(payload: dict[str, Any]) -> BattlefieldSchedulingState:
    """将 AMOS 风格 JSON 映射为现有战场调度状态。"""
    data = payload or {}
    base_lat = float(data.get("base_lat", 30.512))
    base_lon = float(data.get("base_lon", 114.381))
    phase = str(data.get("phase", "recon"))
    now = _parse_dt(data.get("now"))
    platforms = [p for p in (data.get("platforms") or []) if isinstance(p, dict)]
    tasks = [t for t in (data.get("tasks") or []) if isinstance(t, dict)]

    mean_link = _mean_link_quality(data, platforms)
    base_jam = _clamp01(float(data.get("jamming_level", 0.0)))
    # 链路越差 → 有效干扰越高
    jamming_level = _clamp01(max(base_jam, 1.0 - mean_link))

    targets: list[SchedulingTarget] = []
    for task in tasks[:MAX_TARGETS]:
        tid = str(task.get("target_id") or task.get("task_id") or f"T-{len(targets)}")
        damage = _clamp01(float(task.get("damage_score", 0.0) or 0.0))
        conf = _clamp01(float(task.get("confidence", 0.7) or 0.7))
        base_threat = task.get("threat_score")
        if base_threat is None:
            base_threat = task.get("priority", 0.5)
        threat = _clamp01(float(base_threat))
        urgency, in_window = _window_urgency(now, task.get("time_window"))
        if not in_window:
            # 窗外：大幅降权，通常不再触发再攻击
            threat = _clamp01(threat * 0.25)
            needs_reattack = False
        else:
            threat = _clamp01(threat + 0.25 * urgency)
            needs_reattack = damage < 0.55 and threat > 0.4
            if phase == "bda":
                needs_reattack = damage < 0.65 and threat > 0.35

        targets.append(
            SchedulingTarget(
                target_id=tid,
                threat_score=threat,
                damage_score=damage,
                confidence=conf,
                lat=float(task.get("lat", base_lat)),
                lon=float(task.get("lon", base_lon)),
                needs_reattack=needs_reattack,
                class_name=str(task.get("class_name", task.get("task_type", "unknown"))),
            )
        )

    sensors: list[SchedulingSensor] = []
    for p in platforms:
        role = str(p.get("role", "sensor")).lower()
        if role not in ("sensor", "uav", "recon", "isr"):
            continue
        if len(sensors) >= MAX_SENSORS:
            break
        battery = p.get("battery")
        battery_f = _clamp01(float(battery)) if battery is not None else 1.0
        link_q = p.get("link_quality")
        link_f = _clamp01(float(link_q)) if link_q is not None else 1.0
        available = bool(p.get("available", True))
        if battery_f < BATTERY_UNAVAILABLE or link_f < LINK_UNAVAILABLE:
            available = False
        sensors.append(
            SchedulingSensor(
                sensor_id=str(p.get("platform_id", f"S-{len(sensors)}")),
                modality=str(p.get("modality", "eo_ir")),
                available=available,
                load=_clamp01(1.0 - battery_f),
                lat=float(p.get("lat", base_lat)),
                lon=float(p.get("lon", base_lon)),
            )
        )

    strike_assets: list[StrikeAsset] = []
    for p in platforms:
        role = str(p.get("role", "")).lower()
        if role not in ("strike", "fire", "weapon", "artillery"):
            continue
        if len(strike_assets) >= MAX_STRIKE_ASSETS:
            break
        ammo = p.get("ammo")
        ammo_f = _clamp01(float(ammo)) if ammo is not None else 1.0
        battery = p.get("battery")
        battery_f = _clamp01(float(battery)) if battery is not None else 1.0
        link_q = p.get("link_quality")
        link_f = _clamp01(float(link_q)) if link_q is not None else 1.0
        available = bool(p.get("available", True)) and ammo_f > 0.0
        if battery_f < BATTERY_UNAVAILABLE or link_f < LINK_UNAVAILABLE:
            available = False
        strike_assets.append(
            StrikeAsset(
                asset_id=str(p.get("platform_id", f"A-{len(strike_assets)}")),
                asset_type=str(p.get("asset_type", "artillery")),
                available=available,
                remaining_ammo=ammo_f,
            )
        )

    if not sensors:
        sensors.append(
            SchedulingSensor(sensor_id="EO-FWD-1", modality="eo_ir", lat=base_lat, lon=base_lon)
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
        jamming_level=jamming_level,
        phase=phase,
        base_lat=base_lat,
        base_lon=base_lon,
    )
