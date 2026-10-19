"""推理前准备 SensorBatch：预拉取 URL 图像、合并传感器位姿元数据。"""

from __future__ import annotations

from contextvars import Token
from typing import Any, Optional, Tuple

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


def _merge_sensor_meta(
    meta: dict[str, Any], sources: list[Optional[dict[str, Any]]]
) -> dict[str, Any]:
    out = dict(meta)
    for src in sources:
        if not src:
            continue
        for key in _SENSOR_META_KEYS:
            if key in src and src[key] is not None and key not in out:
                out[key] = src[key]
    return out


def prepare_batch_for_inference(batch: SensorBatch) -> Tuple[SensorBatch, Token[Any]]:
    """
    1. 按 mission/work_item 开启当前请求独占的图像缓存；
    2. 从 attachment meta / context 合并传感器位姿到帧 metadata；
    3. 预 GET 所有视觉帧 URL（供感知 + 认知共用）。

    返回 contextvars token；调用方必须在 finally 中传给 finalize_batch_inference，
    这样并发请求与嵌套调用都能恢复自己的上层上下文。
    """
    scope = str(batch.context.get("work_item") or batch.mission_id)
    cache_token = begin_batch_cache(scope)
    try:
        ctx = batch.context or {}
        sensor_telemetry = ctx.get("sensor_telemetry") or {}
        default_telemetry = sensor_telemetry if isinstance(sensor_telemetry, dict) else {}

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

        prepared_context = dict(ctx)
        batch = batch.model_copy(update={"frames": prepared_frames, "context": prepared_context})
        visual = [
            f.model_dump(mode="json")
            for f in batch.frames
            if f.modality in (SensorModality.EO_IR, SensorModality.SAR)
        ]
        loaded = prefetch_visual_frames(visual)
        batch.context["images_prefetched"] = loaded
        return batch, cache_token
    except BaseException:
        # 预取失败前尚未把 token 交给调用方，必须在这里恢复父上下文。
        end_batch_cache(cache_token)
        raise


def finalize_batch_inference(cache_token: Optional[Token[Any]] = None) -> None:
    end_batch_cache(cache_token)
