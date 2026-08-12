"""intelligence_packet → 下游算法输入适配。"""

from __future__ import annotations

from typing import Any

from agent.track_packet import packet_to_trajectory_request


def extract_intelligence_packet(payload_or_output: dict[str, Any]) -> dict[str, Any]:
    """从 Commander output / context entry / 完整响应中取出 intelligence_packet。"""
    if not isinstance(payload_or_output, dict):
        return {}
    if "tracks" in payload_or_output and "targets" in payload_or_output:
        return payload_or_output
    output = payload_or_output.get("output")
    if isinstance(output, dict) and isinstance(output.get("intelligence_packet"), dict):
        return output["intelligence_packet"]
    packet = payload_or_output.get("intelligence_packet")
    if isinstance(packet, dict):
        return packet
    value = payload_or_output.get("value")
    if isinstance(value, dict):
        return extract_intelligence_packet(value)
    return {}


def to_trajectory_predictor_input(packet_or_output: dict[str, Any]) -> dict[str, Any]:
    """供航迹预测 Agent：只取 tracks + history_path。"""
    packet = extract_intelligence_packet(packet_or_output)
    return packet_to_trajectory_request(packet)


def to_decision_targets(packet_or_output: dict[str, Any]) -> list[dict[str, Any]]:
    """供决策/评估/火力：只取 targets。"""
    packet = extract_intelligence_packet(packet_or_output)
    return list(packet.get("targets") or [])


def to_task_schedule(packet_or_output: dict[str, Any]) -> dict[str, Any] | None:
    """供调度：只取 task_schedule。"""
    packet = extract_intelligence_packet(packet_or_output)
    schedule = packet.get("task_schedule")
    return schedule if isinstance(schedule, dict) else None


def to_output_attachments(packet_or_output: dict[str, Any]) -> list[dict[str, Any]]:
    """供可视化 / BDA：只取 output_attachments。"""
    packet = extract_intelligence_packet(packet_or_output)
    return list(packet.get("output_attachments") or [])
