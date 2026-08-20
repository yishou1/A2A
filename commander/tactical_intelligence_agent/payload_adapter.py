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


_SENSOR_META_KEYS = (
    "platform_lat",
    "platform_lon",
    "altitude_m",
    "heading_deg",
    "depression_angle_deg",
    "gimbal_pitch_deg",
    "fov_deg",
    "ground_elevation_m",
    "sea_surface_elevation_m",
    "resolution",
)


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


def _first_present(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _normalize_detection(
    item: dict[str, Any],
    *,
    default_sensor_id: str | None = None,
) -> dict[str, Any]:
    metadata = dict(item.get("metadata") or {})
    item_geo = item.get("geo") if isinstance(item.get("geo"), dict) else {}
    metadata_geo = metadata.get("geo") if isinstance(metadata.get("geo"), dict) else {}
    lat = _first_present(item_geo.get("lat"), metadata_geo.get("lat"), item.get("lat"))
    lon = _first_present(
        item_geo.get("lon"),
        item_geo.get("lng"),
        metadata_geo.get("lon"),
        metadata_geo.get("lng"),
        item.get("lon"),
        item.get("lng"),
    )
    alt_m = _first_present(
        item_geo.get("alt_m"),
        item_geo.get("alt"),
        metadata_geo.get("alt_m"),
        metadata_geo.get("alt"),
        item.get("alt_m"),
        item.get("alt"),
        0.0,
    )
    geo = dict(item_geo or metadata_geo)
    if lat is not None and lon is not None:
        geo.update({"lat": lat, "lon": lon, "alt_m": alt_m})

    confidence = _first_present(item.get("confidence"), metadata.get("confidence"), 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    track_id = _first_present(
        item.get("track_id"),
        item.get("target_id"),
        item.get("contact_id"),
        metadata.get("amos_track_id"),
        metadata.get("source_contact_id"),
    )
    return {
        **item,
        "track_id": track_id,
        "sensor_id": item.get("sensor_id") or default_sensor_id,
        "class_name": _first_present(
            item.get("class_name"),
            item.get("classification"),
            item.get("object_type"),
            metadata.get("classification"),
            metadata.get("object_type"),
            "unknown",
        ),
        "confidence": confidence,
        "bbox": item.get("bbox") or [0.0, 0.0, 0.0, 0.0],
        "geo": geo,
        "metadata": metadata,
    }


def _frames_from_input(input_payload: dict[str, Any]) -> list[SensorFrame]:
    frames: list[SensorFrame] = []
    contacts = input_payload.get("contacts")
    if isinstance(contacts, list) and contacts:
        # Preserve mission contacts as detections when no perception frame exists.
        detections = []
        for item in contacts:
            if not isinstance(item, dict):
                continue
            detection = _normalize_detection(item, default_sensor_id="CONTACTS-CTX")
            detection["metadata"] = {
                **detection["metadata"],
                "source_contact": item,
            }
            detections.append(detection)
        frames.append(
            SensorFrame(
                sensor_id="CONTACTS-CTX",
                modality=SensorModality.RADAR,
                payload={"detections": detections, "scene": {}},
                metadata={"source": "mission_input.contacts"},
            )
        )
    perception_frames = input_payload.get("perception_frames")
    if isinstance(perception_frames, list):
        for index, raw in enumerate(perception_frames):
            if not isinstance(raw, dict):
                continue
            raw_detections = raw.get("detections")
            if not isinstance(raw_detections, list):
                continue
            sensor_id = str(raw.get("sensor_id") or raw.get("task_id") or f"PERCEPTION-{index:03d}")
            detections = [
                _normalize_detection(item, default_sensor_id=sensor_id)
                for item in raw_detections
                if isinstance(item, dict)
            ]
            scene = raw.get("scene") if isinstance(raw.get("scene"), dict) else {}
            frames.append(
                SensorFrame(
                    sensor_id=sensor_id,
                    modality=SensorModality.RADAR,
                    payload={"detections": detections, "scene": scene},
                    metadata={"source": "bpel_mission_input", "frame_index": raw.get("frame_index", index)},
                )
            )
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


def _observable_track_features(input_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    features: dict[str, dict[str, Any]] = {}
    for contact in input_payload.get("contacts") or []:
        if not isinstance(contact, dict):
            continue
        metadata = contact.get("metadata") if isinstance(contact.get("metadata"), dict) else {}
        track_id = _first_present(
            contact.get("track_id"),
            contact.get("contact_id"),
            metadata.get("amos_track_id"),
        )
        if not track_id:
            continue
        sensor_sources = list(metadata.get("sensor_sources") or [])
        features[str(track_id)] = {
            "domain_hint": metadata.get("domain_hint"),
            "prior_classification": _first_present(
                contact.get("classification"),
                metadata.get("prior_classification"),
                metadata.get("classification"),
            ),
            "speed_kts": _first_present(contact.get("speed_kts"), metadata.get("speed_kts")),
            "ais_observed": bool(metadata.get("ais_observed")) or any(
                str(source).casefold() == "ais" for source in sensor_sources
            ),
            "sensor_sources": sensor_sources,
        }
    return features


def _normalize_input_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """兼容飞书/lzh 外壳：业务字段可在 input 或 input.agent_request。"""
    input_payload = dict(payload.get("input") or {})
    nested_payload = input_payload.get("agent_request")
    if not isinstance(nested_payload, dict):
        nested_payload = input_payload.get("mission_input")
    if isinstance(nested_payload, dict):
        merged = dict(nested_payload)
        for key, value in input_payload.items():
            if key in {"agent_request", "mission_input"}:
                continue
            # 外层同名字段不覆盖 agent_request 内已有值
            if key not in merged or merged.get(key) in (None, "", {}, []):
                merged[key] = value
        return merged
    return input_payload


def commander_payload_to_batch(payload: dict[str, Any]) -> SensorBatch:
    """
    将 Commander sendMessage 载荷转为 SensorBatch。

    - attachments：仅接受对象存储引用（workflow_payloads 校验）
    - input / input.agent_request：来自 BPEL/飞书外壳业务字段（如 recon_report）
    - 无 mock 兜底：缺少附件与业务输入时直接失败，供真实场景演练
    """
    workflow_id = payload.get("workflow_id") or payload.get("work_item") or "WF-UNKNOWN"
    command = payload.get("command") or "process_intelligence"

    attachments = normalize_attachments(payload.get("attachments"))
    frames = [_frame_from_attachment(item, index) for index, item in enumerate(attachments)]

    input_payload = _normalize_input_payload(payload)
    upstream_context = dict(payload.get("context") or {})
    context_mission = upstream_context.get("mission_input")
    if isinstance(context_mission, dict):
        for key, value in context_mission.items():
            if key not in input_payload or input_payload.get(key) in (None, "", {}, []):
                input_payload[key] = value
    for key in ("perception_frames", "recon_report", "sector", "coordinates"):
        if key in upstream_context and (key not in input_payload or input_payload.get(key) in (None, "", {}, [])):
            input_payload[key] = upstream_context[key]
    frames.extend(_frames_from_input(input_payload))

    if not frames:
        raise ValueError(
            "无法从 attachments 或 input 构建传感器帧："
            "真实演练要求至少提供一张传感器附件，或 recon_report/sector/coordinates"
        )

    sensor_telemetry = upstream_context.get("sensor_telemetry") or {}
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
        "sensor_telemetry": sensor_telemetry,
        "georef": upstream_context.get("georef"),
        "output_storage_prefix": upstream_context.get("output_storage_prefix"),
        "recon_report": input_payload.get("recon_report") or upstream_context.get("recon_report"),
        "sector": input_payload.get("sector") or upstream_context.get("sector"),
        "coordinates": input_payload.get("coordinates") or upstream_context.get("coordinates"),
        "attachment_refs": attachments,
        "observable_track_features": _observable_track_features(input_payload),
        "director_checkpoint_id": (
            (input_payload.get("stage_transfer") or {}).get("checkpoint_id")
            if isinstance(input_payload.get("stage_transfer"), dict)
            else None
        ) or (input_payload.get("metadata") or {}).get("director_checkpoint_id"),
        "director_phase": (
            (input_payload.get("stage_transfer") or {}).get("phase")
            if isinstance(input_payload.get("stage_transfer"), dict)
            else None
        ) or (input_payload.get("metadata") or {}).get("director_phase"),
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
