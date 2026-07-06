"""推理前准备 SensorBatch：预拉取 URL 图像、合并传感器位姿元数据。"""

from __future__ import annotations

from typing import Any

from agent.inference.image_cache import begin_batch_cache, end_batch_cache, prefetch_visual_frames
from agent.models.schemas import SensorBatch, SensorModality

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
    "msl_elevation_m",
    "resolution",
    "sensor_id",
    "modality",
)


def _merge_sensor_meta(meta: dict[str, Any], sources: list[dict[str, Any] | None]) -> dict[str, Any]:
    out = dict(meta)
    for src in sources:
        if not src:
            continue
        for key in _SENSOR_META_KEYS:
            if key in src and src[key] is not None and key not in out:
                out[key] = src[key]
    return out


def prepare_batch_for_inference(batch: SensorBatch) -> SensorBatch:
    """
    1. 按 mission/work_item 开启图像缓存
    2. 从 attachment meta / context 合并传感器位姿到帧 metadata
    3. 预 GET 所有视觉帧 URL（供感知 + 认知共用）
    """
    scope = str(batch.context.get("work_item") or batch.mission_id)
    begin_batch_cache(scope)

    ctx = batch.context or {}
    sensor_telemetry = ctx.get("sensor_telemetry") or {}
    if isinstance(sensor_telemetry, dict):
        default_telemetry = sensor_telemetry
    else:
        default_telemetry = {}

    prepared_frames = []
    for frame in batch.frames:
        meta = _merge_sensor_meta(
            dict(frame.metadata or {}),
            [
                (frame.payload or {}).get("attachment_ref", {}).get("meta"),
                default_telemetry,
                ctx.get("georef"),
            ],
        )
        prepared_frames.append(frame.model_copy(update={"metadata": meta}))

    batch = batch.model_copy(update={"frames": prepared_frames})
    visual = [f.model_dump(mode="json") for f in batch.frames if f.modality in (SensorModality.EO_IR, SensorModality.SAR)]
    loaded = prefetch_visual_frames(visual)
    batch.context["images_prefetched"] = loaded
    return batch


def finalize_batch_inference() -> None:
    end_batch_cache()
