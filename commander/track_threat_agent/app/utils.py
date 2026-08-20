"""Shared geometry and scoring utilities for the simulation demo."""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Tuple


EARTH_RADIUS_M = 6_371_000.0


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def meters_to_lat_lon_delta(north_m: float, east_m: float, at_lat: float) -> Tuple[float, float]:
    d_lat = north_m / 111_320.0
    cos_lat = max(0.01, math.cos(math.radians(at_lat)))
    d_lon = east_m / (111_320.0 * cos_lat)
    return d_lat, d_lon


def speed_heading_to_velocity(speed_mps: float, heading_deg: float) -> Tuple[float, float]:
    heading_rad = math.radians(heading_deg)
    vx_east = speed_mps * math.sin(heading_rad)
    vy_north = speed_mps * math.cos(heading_rad)
    return vx_east, vy_north


def velocity_to_speed_heading(vx_east: float, vy_north: float) -> Tuple[float, float]:
    speed = math.hypot(vx_east, vy_north)
    if speed < 1e-6:
        return 0.0, 0.0
    heading = math.degrees(math.atan2(vx_east, vy_north)) % 360.0
    return speed, heading


def project_position(lat: float, lon: float, vx_east: float, vy_north: float, dt_s: float) -> Tuple[float, float]:
    d_lat, d_lon = meters_to_lat_lon_delta(vy_north * dt_s, vx_east * dt_s, lat)
    return lat + d_lat, lon + d_lon


def heading_difference_deg(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def bounding_box(points: Iterable[Dict[str, float]]) -> Dict[str, float]:
    point_list = list(points)
    if not point_list:
        return {"min_lat": 0.0, "max_lat": 0.0, "min_lon": 0.0, "max_lon": 0.0}
    lats = [p["lat"] for p in point_list]
    lons = [p["lon"] for p in point_list]
    return {
        "min_lat": min(lats),
        "max_lat": max(lats),
        "min_lon": min(lons),
        "max_lon": max(lons),
    }


def average_point(points: List[Dict[str, float]]) -> Dict[str, float]:
    if not points:
        return {"lat": 0.0, "lon": 0.0, "alt": 0.0}
    return {
        "lat": sum(p.get("lat", 0.0) for p in points) / len(points),
        "lon": sum(p.get("lon", 0.0) for p in points) / len(points),
        "alt": sum(p.get("alt", 0.0) for p in points) / len(points),
    }


def risk_level(score: float) -> str:
    if score >= 0.72:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"
