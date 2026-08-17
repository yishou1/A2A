"""处理完成后将标注图上传对象存储，返回标准 attachment 引用供下游 Agent 读取。"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from agent.inference.annotate import draw_detections_on_image, encode_jpeg_bytes
from agent.inference.utils import decode_image_from_frame
from agent.models.schemas import PerceptionOutput, SensorBatch, SensorModality
from attachment_uploader import upload_attachment_file
from workflow_payloads import build_attachment_ref

VISUAL_MODALITIES = {SensorModality.EO_IR, SensorModality.SAR}


def _artifact_config(config: dict[str, Any] | None, batch: SensorBatch) -> dict[str, Any]:
    cfg = dict((config or {}).get("artifact_storage") or {})
    ctx = batch.context or {}

    enabled = os.environ.get("TIA_ARTIFACT_ENABLED")
    if enabled is not None:
        cfg["enabled"] = enabled.strip().lower() in {"1", "true", "yes", "on"}

    prefix = os.environ.get("TIA_ARTIFACT_URI_PREFIX") or ctx.get("output_storage_prefix")
    if prefix:
        cfg["uri_prefix"] = str(prefix).rstrip("/")

    upload_url = os.environ.get("TIA_ARTIFACT_UPLOAD_URL")
    if upload_url:
        cfg["upload_url"] = upload_url

    return cfg


def _detections_by_sensor(perception: PerceptionOutput) -> dict[str, list[dict[str, Any]]]:
    """按 sensor_id 聚合检测框。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for det in perception.detections:
        sid = det.sensor_id or det.track_id or "unknown"
        grouped.setdefault(str(sid), []).append(
            {
                "class_name": det.class_name,
                "confidence": det.confidence,
                "bbox": det.bbox,
            }
        )
    return grouped


def publish_processed_artifacts(
    batch: SensorBatch,
    perception: PerceptionOutput,
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    对每个视觉帧生成标注图，上传至对象存储，返回 attachment 引用列表。

    下游 Agent 通过 ``output_attachments[].uri`` 读取，无需 base64 直传。
    """
    cfg = _artifact_config(config, batch)
    if not cfg.get("enabled"):
        return []

    uri_prefix = str(cfg.get("uri_prefix") or "").strip()
    if not uri_prefix:
        raise ValueError(
            "artifact_storage.enabled 但未配置 uri_prefix "
            "(config.artifact_storage.uri_prefix 或 TIA_ARTIFACT_URI_PREFIX)"
        )

    mission_id = batch.mission_id.replace("/", "_")
    detections_by_sensor = _detections_by_sensor(perception)
    output_refs: list[dict[str, Any]] = []
    staging_root = Path(cfg.get("local_staging_dir") or "data/output/artifacts")
    staging_dir = staging_root / mission_id
    staging_dir.mkdir(parents=True, exist_ok=True)

    upload_url = cfg.get("upload_url")
    upload_headers = cfg.get("upload_headers") or {}
    mime_type = str(cfg.get("mime_type") or "image/jpeg")
    kind = str(cfg.get("kind") or "image")

    for frame in batch.frames:
        if frame.modality not in VISUAL_MODALITIES:
            continue

        frame_dict = frame.model_dump(mode="json")
        rgb = decode_image_from_frame(frame_dict)
        if rgb is None:
            continue

        sensor_id = frame.sensor_id
        dets = detections_by_sensor.get(sensor_id, [])
        annotated = draw_detections_on_image(rgb, dets)
        jpeg_bytes = encode_jpeg_bytes(annotated)

        object_name = f"{sensor_id}_det.jpg"
        object_uri = f"{uri_prefix}/{mission_id}/{object_name}"
        local_path = staging_dir / object_name
        local_path.write_bytes(jpeg_bytes)

        source_attachment = (frame.payload or {}).get("attachment_ref") or {}
        source_uri = (frame.payload or {}).get("image_uri") or source_attachment.get("uri")

        meta = {
            "sensor_id": sensor_id,
            "modality": frame.modality.value,
            "source_attachment_uri": source_uri,
            "detection_count": len(dets),
            "artifact_type": "annotated_detection",
        }

        try:
            ref = upload_attachment_file(
                local_path,
                object_uri,
                upload_url=upload_url,
                upload_headers=upload_headers,
                mime_type=mime_type,
                kind=kind,
                name=object_name,
                attachment_id=f"{mission_id}-{sensor_id}-det",
                meta=meta,
            )
        except ValueError:
            # 非 http(s) 且未提供 upload_url/uploader：仅生成本地文件 + 逻辑 URI 引用
            ref = build_attachment_ref(
                object_uri,
                checksum={"algorithm": "sha256", "value": hashlib.sha256(jpeg_bytes).hexdigest()},
                kind=kind,
                mime_type=mime_type,
                size_bytes=len(jpeg_bytes),
                name=object_name,
                attachment_id=f"{mission_id}-{sensor_id}-det",
                meta={**meta, "local_staging_path": str(local_path.resolve())},
            )

        output_refs.append(ref)

    return output_refs
