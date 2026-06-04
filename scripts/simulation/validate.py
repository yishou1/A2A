"""战役模拟结果校验（run_simulation 内置使用）。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent.models.schemas import SemanticIntelligencePacket


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def _trace_count(trace_val: object, suffix: str) -> int:
    m = re.search(r"(\d+)\s+" + re.escape(suffix), str(trace_val))
    return int(m.group(1)) if m else 0


def preflight_detection_check(*, raw_count: int, verified_count: int) -> list[CheckResult]:
    """流水线启动前：确认 RT-DETR + EDL 链路能产生目标。"""
    return [
        CheckResult(
            "RT-DETR 原始检出",
            raw_count >= 1,
            f"raw={raw_count}",
        ),
        CheckResult(
            "EDL 验证后保留",
            verified_count >= 1,
            f"verified={verified_count}",
        ),
    ]


def validate_phase_packet(
    packet: SemanticIntelligencePacket,
    *,
    phase_prefix: str,
    expect_targets_min: int = 1,
    expect_anti_jam: bool = False,
) -> list[CheckResult]:
    results: list[CheckResult] = []

    def add(name: str, ok: bool, detail: str) -> None:
        results.append(CheckResult(name=name, passed=ok, detail=detail))

    add("packet_id", bool(packet.packet_id), packet.packet_id[:8] + "...")
    add("summary 非空", bool(packet.summary.strip()), packet.summary)
    add(
        f"targets >= {expect_targets_min}",
        len(packet.targets) >= expect_targets_min,
        f"实际 {len(packet.targets)}",
    )
    add(
        "semantic_vector",
        len(packet.semantic_vector) > 0,
        f"dim={len(packet.semantic_vector)}",
    )
    routes = packet.routing.get("routes", [])
    add("routing.routes", bool(routes), f"{len(routes)} 条")

    prov = packet.provenance or {}
    add(
        "provenance 三技能",
        all(k in prov for k in ("perception", "cognition", "communication")),
        str(list(prov.keys())),
    )
    add(
        "压缩比 > 1",
        packet.raw_compression_ratio > 1.0,
        f"ratio={packet.raw_compression_ratio:.2f}",
    )

    if expect_anti_jam:
        add(
            "anti_jam_mode",
            packet.routing.get("anti_jam_mode") is True,
            str(packet.routing.get("anti_jam_mode")),
        )
        channels = [r.get("channel") for r in routes]
        add("fhss_backup 信道", "fhss_backup" in channels, str(channels))

    perc = prov.get("perception", {})
    if expect_targets_min >= 1 and isinstance(perc, dict):
        det_n = _trace_count(perc.get("RT-DETR+ODConv", ""), "detections")
        trk_n = _trace_count(perc.get("MOTR+Neural-Kalman", ""), "tracks")
        if det_n == 0 and trk_n == 0:
            add(f"{phase_prefix} 感知有检出", False, str(perc))

    return results
