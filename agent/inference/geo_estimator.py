"""
多域目标三维地理解算：陆地 / 空中 / 海上。

同一入口 `estimate_target_geo`，按目标类型与可用传感器（激光、雷达、尺寸先验）
选择高度模型，输出含 domain、alt_source、alt_confidence 的 geo。
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any

from agent.inference.geolocation import (
    SensorGeorefContext,
    bbox_center,
    intersect_horizontal_plane,
    pixel_ray_enu,
    point_along_ray,
)

_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "air": (
        "airplane",
        "aircraft",
        "helicopter",
        "heli",
        "drone",
        "uav",
        "jet",
        "fighter",
        "plane",
        "rotor",
    ),
    "sea": (
        "ship",
        "boat",
        "vessel",
        "frigate",
        "destroyer",
        "carrier",
        "submarine",
        "naval",
        "warship",
    ),
}

_DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "land": {
        "default": {"vertical_offset_m": 2.0},
        "tank": {"vertical_offset_m": 2.5},
        "armor": {"vertical_offset_m": 2.5},
        "truck": {"vertical_offset_m": 3.0},
        "car": {"vertical_offset_m": 1.5},
        "bus": {"vertical_offset_m": 3.2},
        "person": {"vertical_offset_m": 1.8},
    },
    "air": {
        "default": {"reference_height_m": 5.0},
        "airplane": {"reference_height_m": 8.0},
        "aircraft": {"reference_height_m": 8.0},
        "helicopter": {"reference_height_m": 4.5},
        "drone": {"reference_height_m": 1.2},
        "uav": {"reference_height_m": 1.2},
    },
    "sea": {
        "default": {"vertical_offset_m": 12.0},
        "ship": {"vertical_offset_m": 18.0},
        "frigate": {"vertical_offset_m": 25.0},
        "destroyer": {"vertical_offset_m": 28.0},
        "boat": {"vertical_offset_m": 4.0},
    },
}


class TargetDomain(str, Enum):
    LAND = "land"
    AIR = "air"
    SEA = "sea"


def _norm_class_name(class_name: str) -> str:
    return str(class_name or "unknown").strip().lower().replace("-", "_")


def infer_target_domain(
    class_name: str,
    *,
    explicit_domain: str | None = None,
    config: dict[str, Any] | None = None,
) -> TargetDomain:
    if explicit_domain:
        key = explicit_domain.strip().lower()
        if key in TargetDomain._value2member_map_:
            return TargetDomain(key)

    cfg = config or {}
    overrides = cfg.get("geo_domain_overrides") or {}
    norm = _norm_class_name(class_name)
    if norm in overrides:
        return TargetDomain(str(overrides[norm]).lower())

    for domain, keywords in _DOMAIN_KEYWORDS.items():
        if any(kw in norm for kw in keywords):
            return TargetDomain(domain)
    return TargetDomain.LAND


def _profile_for(domain: TargetDomain, class_name: str, config: dict[str, Any]) -> dict[str, Any]:
    profiles = config.get("geo_target_profiles") or _DEFAULT_PROFILES
    domain_profiles = profiles.get(domain.value) or _DEFAULT_PROFILES[domain.value]
    norm = _norm_class_name(class_name)
    if norm in domain_profiles:
        return dict(domain_profiles[norm])
    for key, prof in domain_profiles.items():
        if key != "default" and key in norm:
            return dict(prof)
    return dict(domain_profiles.get("default") or {})


def _first_float(*values: Any) -> float | None:
    for v in values:
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _estimate_slant_from_bbox(
    bbox: list[float],
    ctx: SensorGeorefContext,
    reference_height_m: float,
) -> float | None:
    h_px = max(float(bbox[3]) - float(bbox[1]), 1.0)
    fov_rad = math.radians(ctx.fov_h_deg)
    fy = (ctx.image_height / 2.0) / max(math.tan(fov_rad / 2.0), 1e-6)
    if reference_height_m <= 0:
        return None
    return max(reference_height_m * fy / h_px, 50.0)


def _resolve_slant_range(
    domain: TargetDomain,
    bbox: list[float],
    ctx: SensorGeorefContext,
    profile: dict[str, Any],
    det_meta: dict[str, Any],
    frame_meta: dict[str, Any],
    batch_context: dict[str, Any] | None,
) -> tuple[float | None, str, float]:
    """返回 (slant_range_m, alt_source, confidence)。"""
    batch_context = batch_context or {}

    laser = _first_float(
        det_meta.get("laser_range_m"),
        frame_meta.get("laser_range_m"),
        batch_context.get("laser_range_m"),
    )
    if laser is not None and laser > 0:
        return laser, "laser_range", 0.92

    radar_range = _first_float(
        det_meta.get("radar_range_m"),
        frame_meta.get("radar_range_m"),
        batch_context.get("radar_range_m"),
    )
    if radar_range is not None and radar_range > 0:
        return radar_range, "radar_range", 0.88

    if domain == TargetDomain.AIR:
        ref_h = float(profile.get("reference_height_m", 5.0))
        est = _estimate_slant_from_bbox(bbox, ctx, ref_h)
        if est is not None:
            return est, "bbox_size_prior", 0.45

    return None, "plane_intersection", 0.55 if domain != TargetDomain.AIR else 0.35


def _surface_elevation(domain: TargetDomain, ctx: SensorGeorefContext) -> float:
    if domain == TargetDomain.SEA:
        return ctx.sea_surface_elevation_m
    return ctx.ground_elevation_m


def estimate_target_geo(
    bbox: list[float],
    ctx: SensorGeorefContext,
    *,
    class_name: str = "unknown",
    det_meta: dict[str, Any] | None = None,
    frame_meta: dict[str, Any] | None = None,
    batch_context: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    prior_geo: dict[str, Any] | None = None,
    smooth_alpha: float = 0.7,
) -> dict[str, Any]:
    cfg = config or {}
    det_meta = det_meta or {}
    frame_meta = frame_meta or {}

    explicit = det_meta.get("target_domain") or frame_meta.get("target_domain")
    domain = infer_target_domain(class_name, explicit_domain=explicit, config=cfg)
    profile = _profile_for(domain, class_name, cfg)

    cx, cy = bbox_center(bbox)
    ray = pixel_ray_enu(cx, cy, ctx)

    slant, alt_source, confidence = _resolve_slant_range(
        domain, bbox, ctx, profile, det_meta, frame_meta, batch_context
    )

    vertical_offset = float(profile.get("vertical_offset_m", 0.0))

    if slant is not None and alt_source in {"laser_range", "radar_range"}:
        raw = point_along_ray(ctx, ray, slant)
        if domain == TargetDomain.LAND:
            raw["alt_m"] = raw["alt_m"] + vertical_offset * 0.5
        elif domain == TargetDomain.SEA:
            raw["alt_m"] = max(raw["alt_m"], ctx.sea_surface_elevation_m + vertical_offset)
        geo_method = f"{domain.value}_{alt_source}"
    elif domain == TargetDomain.AIR and slant is not None and alt_source == "bbox_size_prior":
        raw = point_along_ray(ctx, ray, slant)
        geo_method = "air_bbox_size_prior"
    elif domain == TargetDomain.AIR:
        raw = intersect_horizontal_plane(ctx, ray, ctx.ground_elevation_m)
        raw["alt_m"] = ctx.platform_alt_m * 0.6 + ctx.ground_elevation_m * 0.4
        geo_method = "air_weak_prior"
        alt_source = "weak_altitude_prior"
        confidence = 0.25
    else:
        plane_alt = _surface_elevation(domain, ctx)
        raw = intersect_horizontal_plane(ctx, ray, plane_alt)
        raw["alt_m"] = plane_alt + vertical_offset
        geo_method = f"{domain.value}_surface_intersection"
        if domain == TargetDomain.LAND:
            alt_source = "dem_surface" if cfg.get("dem_path") else "ground_surface"
            confidence = 0.72
        else:
            alt_source = "msl_surface"
            confidence = 0.68

    geo: dict[str, Any] = {
        "lat": round(raw["lat"], 6),
        "lon": round(raw["lon"], 6),
        "alt_m": round(raw["alt_m"], 1),
        "slant_range_m": round(float(raw.get("slant_range_m", 0.0)), 1),
        "domain": domain.value,
        "alt_source": alt_source,
        "alt_confidence": round(confidence, 2),
        "geo_method": geo_method,
        "vertical_offset_m": round(vertical_offset, 2),
        "class_name": class_name,
    }

    if prior_geo and all(k in prior_geo for k in ("lat", "lon", "alt_m")):
        a = smooth_alpha
        geo["lat"] = round(a * geo["lat"] + (1 - a) * float(prior_geo["lat"]), 6)
        geo["lon"] = round(a * geo["lon"] + (1 - a) * float(prior_geo["lon"]), 6)
        geo["alt_m"] = round(a * geo["alt_m"] + (1 - a) * float(prior_geo["alt_m"]), 1)
        geo["geo_method"] = f"{geo_method}+temporal_smooth"

    return geo
