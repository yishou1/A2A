"""相机几何与传感器位姿解析（像素 → ENU 射线）。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

_M_PER_DEG_LAT = 111_320.0


@dataclass(frozen=True)
class SensorGeorefContext:
    platform_lat: float
    platform_lon: float
    platform_alt_m: float
    image_width: int
    image_height: int
    fov_h_deg: float
    heading_deg: float
    depression_angle_deg: float
    ground_elevation_m: float
    sea_surface_elevation_m: float


def _normalize_vec(v: tuple[float, float, float]) -> tuple[float, float, float]:
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if n < 1e-12:
        return (0.0, 0.0, -1.0)
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _mat_vec_mul(m: list[list[float]], v: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def _camera_to_enu_rotation(heading_deg: float, depression_deg: float) -> list[list[float]]:
    h = math.radians(heading_deg)
    d = math.radians(depression_deg)
    sh, ch = math.sin(h), math.cos(h)
    sd, cd = math.sin(d), math.cos(d)

    bz = _normalize_vec((sh * cd, ch * cd, -sd))
    up = (0.0, 0.0, 1.0)
    bx = _normalize_vec(_cross(up, bz))
    by = _normalize_vec(_cross(bz, bx))
    return [
        [bx[0], by[0], bz[0]],
        [bx[1], by[1], bz[1]],
        [bx[2], by[2], bz[2]],
    ]


def _parse_resolution(resolution: str) -> tuple[int, int] | None:
    match = re.match(r"(\d+)\s*x\s*(\d+)", str(resolution), re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _coerce_float(value: Any, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _ao_center(batch_context: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not batch_context:
        return None, None
    ao = batch_context.get("area_of_operations") or {}
    center = ao.get("center") or {}
    lat = center.get("lat")
    lon = center.get("lon")
    if lat is None or lon is None:
        return None, None
    return float(lat), float(lon)


def parse_sensor_georef_context(
    visual_frame: dict[str, Any] | None,
    config: dict[str, Any] | None,
    *,
    batch_context: dict[str, Any] | None = None,
    image_width: int | None = None,
    image_height: int | None = None,
) -> SensorGeorefContext:
    cfg = config or {}
    meta = (visual_frame or {}).get("metadata") or {}
    payload = (visual_frame or {}).get("payload") or {}

    ao_lat, ao_lon = _ao_center(batch_context)

    platform_lat = _coerce_float(
        meta.get("platform_lat"),
        _coerce_float(ao_lat, _coerce_float(cfg.get("base_lat"), 30.512)),
    )
    platform_lon = _coerce_float(
        meta.get("platform_lon"),
        _coerce_float(ao_lon, _coerce_float(cfg.get("base_lon"), 114.381)),
    )
    platform_alt_m = _coerce_float(
        meta.get("altitude_m"),
        _coerce_float(cfg.get("default_platform_altitude_m"), 3200.0),
    )

    width = image_width
    height = image_height
    if width is None or height is None:
        parsed = _parse_resolution(meta.get("resolution", ""))
        if parsed:
            width, height = parsed
    width = int(width or cfg.get("default_image_width", 640))
    height = int(height or cfg.get("default_image_height", 640))

    fov_h_deg = _coerce_float(
        payload.get("fov_deg"),
        _coerce_float(meta.get("fov_deg"), _coerce_float(cfg.get("default_fov_h_deg"), 45.0)),
    )
    heading_deg = _coerce_float(
        meta.get("heading_deg"),
        _coerce_float(cfg.get("default_platform_heading_deg"), 0.0),
    )
    depression_angle_deg = _coerce_float(
        meta.get("depression_angle_deg"),
        _coerce_float(
            meta.get("gimbal_pitch_deg"),
            _coerce_float(cfg.get("default_depression_angle_deg"), 75.0),
        ),
    )
    if depression_angle_deg < 0:
        depression_angle_deg = 90.0 + depression_angle_deg

    ground_elevation_m = _coerce_float(
        meta.get("ground_elevation_m"),
        _coerce_float(
            (batch_context or {}).get("ground_elevation_m"),
            _coerce_float(cfg.get("ground_elevation_m"), 120.0),
        ),
    )
    sea_surface_elevation_m = _coerce_float(
        meta.get("sea_surface_elevation_m"),
        _coerce_float(
            meta.get("msl_elevation_m"),
            _coerce_float(
                (batch_context or {}).get("sea_surface_elevation_m"),
                _coerce_float(cfg.get("sea_surface_elevation_m"), 0.0),
            ),
        ),
    )

    return SensorGeorefContext(
        platform_lat=platform_lat,
        platform_lon=platform_lon,
        platform_alt_m=platform_alt_m,
        image_width=width,
        image_height=height,
        fov_h_deg=fov_h_deg,
        heading_deg=heading_deg,
        depression_angle_deg=depression_angle_deg,
        ground_elevation_m=ground_elevation_m,
        sea_surface_elevation_m=sea_surface_elevation_m,
    )


def bbox_center(bbox: list[float]) -> tuple[float, float]:
    return (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0


def pixel_ray_enu(u: float, v: float, ctx: SensorGeorefContext) -> tuple[float, float, float]:
    """像素 → 单位视线向量（ENU：东、北、天）。"""
    cx = ctx.image_width / 2.0
    cy = ctx.image_height / 2.0
    fov_rad = math.radians(ctx.fov_h_deg)
    fx = (ctx.image_width / 2.0) / max(math.tan(fov_rad / 2.0), 1e-6)
    fy = fx
    ray_cam = _normalize_vec(((u - cx) / fx, (v - cy) / fy, 1.0))
    rot = _camera_to_enu_rotation(ctx.heading_deg, ctx.depression_angle_deg)
    return _mat_vec_mul(rot, ray_cam)


def enu_offset_to_wgs84(
    ctx: SensorGeorefContext,
    east_m: float,
    north_m: float,
    up_m: float,
) -> dict[str, float]:
    lat = ctx.platform_lat + north_m / _M_PER_DEG_LAT
    cos_lat = math.cos(math.radians(ctx.platform_lat))
    lon = ctx.platform_lon + east_m / max(_M_PER_DEG_LAT * cos_lat, 1e-6)
    alt_m = ctx.platform_alt_m + up_m
    return {"lat": lat, "lon": lon, "alt_m": alt_m}


def intersect_horizontal_plane(
    ctx: SensorGeorefContext,
    ray_enu: tuple[float, float, float],
    plane_alt_m: float,
) -> dict[str, float]:
    """视线与水平面 plane_alt_m 求交，返回 WGS84 与沿射线距离。"""
    ray_z = ray_enu[2]
    delta_u = plane_alt_m - ctx.platform_alt_m
    if abs(ray_z) < 1e-9:
        slant = abs(delta_u)
        return {
            **enu_offset_to_wgs84(ctx, 0.0, 0.0, delta_u),
            "slant_range_m": slant,
        }
    t = delta_u / ray_z
    if t <= 0:
        t = abs(delta_u) / max(abs(ray_z), 1e-6)
    east_m = t * ray_enu[0]
    north_m = t * ray_enu[1]
    up_m = t * ray_enu[2]
    return {
        **enu_offset_to_wgs84(ctx, east_m, north_m, up_m),
        "slant_range_m": t,
    }


def point_along_ray(
    ctx: SensorGeorefContext,
    ray_enu: tuple[float, float, float],
    slant_range_m: float,
) -> dict[str, float]:
    east_m = slant_range_m * ray_enu[0]
    north_m = slant_range_m * ray_enu[1]
    up_m = slant_range_m * ray_enu[2]
    return {
        **enu_offset_to_wgs84(ctx, east_m, north_m, up_m),
        "slant_range_m": slant_range_m,
    }
