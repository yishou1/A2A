"""Commander 任务载荷 → SensorBatch（遵守附件对象存储协议）。"""

from __future__ import annotations

import os
from typing import Any

from agent.models.schemas import SensorBatch, SensorFrame, SensorModality
from workflow_payloads import normalize_attachments


def _modality_for_attachment(attachment: dict[str, Any]) -> SensorModality:
    kind = str(attachment.get("kind", "other")).lower()
    mime = str(attachment.get("mime_type", "")).lower()
    meta = attachment.get("meta") or {}

    if kind in {"sar", "radar"}:
        return SensorModality.SAR if kind == "sar" else SensorModality.RADAR
    if kind in {"eo", "eo_ir", "image", "video_frame"}:
        return SensorModality.EO_IR
    if kind in {"text", "report", "document"}:
        return SensorModality.TEXT_REPORT
    if "sar" in mime:
        return SensorModality.SAR
    if mime.startswith("text/"):
        return SensorModality.TEXT_REPORT
    if meta.get("modality"):
        return SensorModality(str(meta["modality"]))
    return SensorModality.EO_IR


def _frame_from_attachment(attachment: dict[str, Any], index: int) -> SensorFrame:
    attachment_id = attachment.get("id") or f"att-{index:03d}"
    modality = _modality_for_attachment(attachment)
    meta = dict(attachment.get("meta") or {})
    meta["attachment_uri"] = attachment["uri"]
    meta["checksum"] = attachment.get("checksum")

    payload: dict[str, Any] = {"attachment_ref": attachment}
    if modality == SensorModality.TEXT_REPORT:
        payload["text"] = meta.get("text") or f"attachment:{attachment['uri']}"
    else:
        payload["image_uri"] = attachment["uri"]

    return SensorFrame(
        sensor_id=str(meta.get("sensor_id") or attachment_id),
        modality=modality,
        payload=payload,
        metadata=meta,
    )


def _frames_from_input(input_payload: dict[str, Any]) -> list[SensorFrame]:
    frames: list[SensorFrame] = []
    recon_report = input_payload.get("recon_report")
    if recon_report:
        frames.append(
            SensorFrame(
                sensor_id="RECON-TEXT",
                modality=SensorModality.TEXT_REPORT,
                payload={"text": str(recon_report)},
                metadata={"source": "recon_report"},
            )
        )

    sector = input_payload.get("sector") or input_payload.get("Sector_A")
    if sector:
        frames.append(
            SensorFrame(
                sensor_id="SECTOR-CTX",
                modality=SensorModality.TEXT_REPORT,
                payload={"text": f"sector={sector}"},
                metadata={"source": "sector"},
            )
        )

    coordinates = input_payload.get("coordinates") or input_payload.get("StrikeCoordinates")
    if coordinates:
        frames.append(
            SensorFrame(
                sensor_id="GEO-CTX",
                modality=SensorModality.TELEMETRY,
                payload={"coordinates": coordinates},
                metadata={"source": "coordinates"},
            )
        )
    return frames


def _mock_visual_frame() -> SensorFrame:
    """无附件时的 mock 联调帧（仅 use_mock 模式）。"""
    return SensorFrame(
        sensor_id="MOCK-EO",
        modality=SensorModality.EO_IR,
        payload={"image_base64": "mock"},
        metadata={"source": "mock_fallback"},
    )


def commander_payload_to_batch(payload: dict[str, Any], *, allow_mock_fallback: bool = True) -> SensorBatch:
    """
    将 Commander sendMessage 载荷转为 SensorBatch。

    - attachments：仅接受对象存储引用（workflow_payloads 校验）
    - input/context：来自 BPEL 上游变量（如 recon_report）
    """
    workflow_id = payload.get("workflow_id") or payload.get("work_item") or "WF-UNKNOWN"
    command = payload.get("command") or "process_intelligence"

    attachments = normalize_attachments(payload.get("attachments"))
    frames = [_frame_from_attachment(item, index) for index, item in enumerate(attachments)]

    input_payload = dict(payload.get("input") or {})
    frames.extend(_frames_from_input(input_payload))

    if not frames and allow_mock_fallback:
        frames.append(_mock_visual_frame())

    if not frames:
        raise ValueError("无法从 attachments 或 input 构建传感器帧")

    upstream_context = dict(payload.get("context") or {})
    batch_context: dict[str, Any] = {
        "command": command,
        "work_item": payload.get("work_item"),
        "activatity_id": payload.get("activatity_id"),
        "workflow_id": workflow_id,
        "jamming_level": float(upstream_context.get("jamming_level", 0.0)),
        "subscriber_agents": upstream_context.get("subscriber_agents")
        or ["commander", "artillery", "evaluator"],
        "knowledge_base": upstream_context.get("knowledge_base") or [],
        "battlefield_situation": upstream_context.get("battlefield_situation"),
        "area_of_operations": upstream_context.get("area_of_operations"),
        "ground_elevation_m": upstream_context.get("ground_elevation_m"),
        "sea_surface_elevation_m": upstream_context.get("sea_surface_elevation_m"),
        "laser_range_m": upstream_context.get("laser_range_m"),
        "radar_range_m": upstream_context.get("radar_range_m"),
        "recon_report": input_payload.get("recon_report") or upstream_context.get("recon_report"),
        "sector": input_payload.get("sector") or upstream_context.get("sector"),
        "coordinates": input_payload.get("coordinates") or upstream_context.get("coordinates"),
        "attachment_refs": attachments,
    }

    if os.environ.get("TIA_ALLOW_INLINE_FRAMES", "0") == "1":
        inline_frames = payload.get("sensor_frames")
        if isinstance(inline_frames, list):
            for item in inline_frames:
                frames.append(SensorFrame.model_validate(item))

    return SensorBatch(
        mission_id=str(workflow_id),
        frames=frames,
        context=batch_context,
    )
