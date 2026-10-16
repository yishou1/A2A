"""抗干扰情报分发路由 — 多属性决策（SAW）工业主路径。

出处与可行性（有迹可循）
------------------------
1. **Simple Additive Weighting (SAW)**  
   Hwang, C.-L. & Yoon, K. (1981). *Multiple Attribute Decision Making:
   Methods and Applications*. Springer.  
   工业界最常用的多准则选型方法之一：对各方案属性归一化后加权求和。

2. **抗干扰信道切换（ECCM）**  
   在干扰升高时从宽带语义信道切换到跳频/备份链路，是电子对抗中的标准做法  
   （Frequency-Hopping Spread Spectrum, FHSS；战术数据链备份信道）。

3. **按订阅角色的优先级分发**  
   指挥 / 火力 / 后勤等消费者优先级不同，属于任务系统中的 QoS 路由常识，
   与 IETF DiffServ / 战术消息优先级（如 IMMEDIATE/FLASH）同一思路。

本模块是 **确定性、可复现、可审计** 的工业默认路径；可选的学习增强见
`agent.inference.models.ppo_channel_policy`（PPO 信道策略，Schulman et al. 2017）。
"""

from __future__ import annotations

from typing import Any

# 信道方案：属性已按 [0,1] 有利方向编码（越大越好）
CHANNELS: tuple[str, ...] = ("semantic_rf", "fhss_backup", "satcom_relay")

# 静态属性表（可被 config 覆盖）
# anti_jam: 抗干扰能力；capacity: 语义载荷吞吐；latency: 低时延（越大越好）
CHANNEL_ATTRS: dict[str, dict[str, float]] = {
    "semantic_rf": {"anti_jam": 0.35, "capacity": 1.00, "latency": 0.85},
    "fhss_backup": {"anti_jam": 0.95, "capacity": 0.55, "latency": 0.65},
    "satcom_relay": {"anti_jam": 0.70, "capacity": 0.70, "latency": 0.45},
}

# SAW 默认权重（和为 1）
DEFAULT_WEIGHTS: dict[str, float] = {
    "anti_jam": 0.45,
    "capacity": 0.20,
    "latency": 0.15,
    "threat_urgency": 0.20,
}

# 订阅方角色优先级（越大越优先保障）
ROLE_PRIORITY: dict[str, float] = {
    "command_agent": 1.00,
    "fire_control_agent": 0.92,
    "logistics_agent": 0.70,
    "intel_agent": 0.85,
    "ew_agent": 0.80,
}

PROVENANCE = {
    "method": "SAW (Simple Additive Weighting) multi-criteria anti-jam routing",
    "references": [
        {
            "key": "SAW",
            "cite": "Hwang & Yoon (1981) Multiple Attribute Decision Making",
            "role": "primary decision rule",
        },
        {
            "key": "FHSS_ECCM",
            "cite": "Frequency-hopping / backup-link ECCM channel switching (classic EW practice)",
            "role": "channel candidate design under jamming",
        },
        {
            "key": "QoS_priority",
            "cite": "Role-based message priority (DiffServ / tactical precedence)",
            "role": "destination ordering and reliability shaping",
        },
        {
            "key": "PPO_optional",
            "cite": "Schulman et al. (2017) Proximal Policy Optimization — optional learned channel policy",
            "role": "optional neural enhancement (route_mode=ppo_channel_policy)",
        },
    ],
    "industrial_default": True,
}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _threat_stats(packet: dict[str, Any]) -> tuple[int, int, float]:
    targets = packet.get("targets") or []
    n = len(targets) if isinstance(targets, list) else 0
    n_high = 0
    if isinstance(targets, list):
        for t in targets:
            if isinstance(t, dict) and str(t.get("threat_level", "")).lower() == "high":
                n_high += 1
    urgency = (n_high / n) if n else 0.0
    return n, n_high, urgency


def _role_priority(agent: str, overrides: dict[str, float] | None = None) -> float:
    table = {**ROLE_PRIORITY, **(overrides or {})}
    if agent in table:
        return float(table[agent])
    # 未知角色：中等优先级
    return 0.60


def score_channel(
    channel: str,
    *,
    jamming: float,
    threat_urgency: float,
    weights: dict[str, float],
    channel_attrs: dict[str, dict[str, float]],
) -> dict[str, float]:
    """对单个信道计算 SAW 效用与分项得分（便于审计）。"""
    jam = _clamp01(jamming)
    attrs = channel_attrs.get(channel) or CHANNEL_ATTRS["semantic_rf"]
    # 抗干扰有效分：静态抗干扰 × (1 - 对该信道敏感的干扰折损)
    jam_sensitivity = 1.0 - float(attrs.get("anti_jam", 0.5))
    anti_jam_eff = float(attrs["anti_jam"]) * (1.0 - jam * jam_sensitivity)
    capacity = float(attrs["capacity"]) * (1.0 - 0.25 * jam)
    latency = float(attrs["latency"])
    # 高威胁时更看重抗干扰与可达性，威胁紧迫度作为独立准则
    threat_term = (0.5 + 0.5 * float(attrs["anti_jam"])) * (0.4 + 0.6 * threat_urgency)

    parts = {
        "anti_jam": _clamp01(anti_jam_eff),
        "capacity": _clamp01(capacity),
        "latency": _clamp01(latency),
        "threat_urgency": _clamp01(threat_term),
    }
    utility = sum(float(weights.get(k, 0.0)) * v for k, v in parts.items())
    return {"utility": round(utility, 6), **{k: round(v, 6) for k, v in parts.items()}}


def select_channel(
    *,
    jamming: float,
    threat_urgency: float,
    weights: dict[str, float] | None = None,
    channel_attrs: dict[str, dict[str, float]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """返回最优信道 + 全信道决策轨迹（可审计）。"""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    # 归一化权重
    s = sum(max(0.0, float(v)) for v in w.values()) or 1.0
    w = {k: max(0.0, float(v)) / s for k, v in w.items()}
    attrs = channel_attrs or CHANNEL_ATTRS

    ranked: list[dict[str, Any]] = []
    for ch in CHANNELS:
        sc = score_channel(
            ch,
            jamming=jamming,
            threat_urgency=threat_urgency,
            weights=w,
            channel_attrs=attrs,
        )
        ranked.append({"channel": ch, **sc})
    ranked.sort(key=lambda r: r["utility"], reverse=True)
    best = str(ranked[0]["channel"])
    return best, ranked


def mcdm_route(
    packet: dict[str, Any],
    agents: list[str],
    jamming: float,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """工业默认路由：SAW 选信道 + 角色优先级塑造可靠性。"""
    cfg = config or {}
    jam = _clamp01(jamming)
    default_agents = list(agents) or ["command_agent", "fire_control_agent", "logistics_agent"]
    _, n_high, urgency = _threat_stats(packet)
    priority = "high" if n_high > 0 or urgency >= 0.5 else "normal"

    weights = cfg.get("saw_weights") if isinstance(cfg.get("saw_weights"), dict) else None
    role_overrides = cfg.get("role_priority") if isinstance(cfg.get("role_priority"), dict) else None
    channel_attrs = cfg.get("channel_attrs") if isinstance(cfg.get("channel_attrs"), dict) else None

    best_channel, decision_trace = select_channel(
        jamming=jam,
        threat_urgency=urgency,
        weights=weights,
        channel_attrs=channel_attrs,
    )

    # 强干扰强制进入抗干扰模式：至少 fhss/satcom
    anti_jam_mode = jam >= float(cfg.get("anti_jam_threshold", 0.5))
    if anti_jam_mode and best_channel == "semantic_rf":
        best_channel = "fhss_backup"

    routes: list[dict[str, Any]] = []
    # 按角色优先级排序分发顺序（指挥优先）
    ordered = sorted(
        default_agents,
        key=lambda a: _role_priority(str(a), role_overrides),
        reverse=True,
    )
    best_util = float(decision_trace[0]["utility"]) if decision_trace else 0.5
    for rank, agent in enumerate(ordered):
        role_p = _role_priority(str(agent), role_overrides)
        # 可靠性：信道效用 × 角色优先级 × 干扰折损 − 排队次序惩罚
        reliability = best_util * (0.55 + 0.45 * role_p) * (1.0 - 0.35 * jam) - rank * 0.02
        reliability = _clamp01(reliability)
        routes.append(
            {
                "destination": agent,
                "channel": best_channel,
                "reliability": round(max(0.1, reliability), 3),
                "priority": priority,
                "role_priority": round(role_p, 3),
                "route_policy": "mcdm_saw",
                "decision_rule": "SAW",
            }
        )

    return {
        "routes": routes,
        "anti_jam_mode": anti_jam_mode,
        "broadcast_summary": packet.get("summary", ""),
        "route_mode": "mcdm_saw",
        "selected_channel": best_channel,
        "algorithm_provenance": {
            **PROVENANCE,
            "jamming_level": jam,
            "threat_urgency": round(urgency, 4),
            "high_threat_count": n_high,
            "saw_weights": weights or DEFAULT_WEIGHTS,
            "channel_decision_trace": decision_trace,
        },
    }
