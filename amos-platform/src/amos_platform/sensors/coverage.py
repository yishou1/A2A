"""Sensor coverage models and scenario sensor name normalization."""

from __future__ import annotations

import math


SENSOR_COVERAGE: dict[str, dict] = {
    "ORBITAL_SAR": {"range_nm": 1000, "fov_deg": 360, "detect_air": False, "detect_ground": True, "detect_maritime": True},
    "AESA_RADAR": {"range_nm": 80, "fov_deg": 120, "detect_air": True, "detect_ground": True, "detect_maritime": True},
    "AEW_RADAR": {"range_nm": 200, "fov_deg": 360, "detect_air": True, "detect_ground": False, "detect_maritime": True},
    "EO/IR": {"range_nm": 15, "fov_deg": 60, "detect_air": True, "detect_ground": True, "detect_maritime": True},
    "SAR": {"range_nm": 40, "fov_deg": 90, "detect_air": False, "detect_ground": True, "detect_maritime": True},
    "LIDAR": {"range_nm": 2, "fov_deg": 30, "detect_air": False, "detect_ground": True, "detect_maritime": False},
    "RADAR": {"range_nm": 25, "fov_deg": 360, "detect_air": True, "detect_ground": False, "detect_maritime": True},
    "NAV_RADAR": {"range_nm": 24, "fov_deg": 360, "detect_air": False, "detect_ground": False, "detect_maritime": True},
    "GROUND_RADAR": {"range_nm": 6.5, "fov_deg": 360, "detect_air": False, "detect_ground": True, "detect_maritime": False},
    "SONAR": {"range_nm": 10, "fov_deg": 360, "detect_air": False, "detect_ground": False, "detect_maritime": True},
    "ACOUSTIC": {"range_nm": 3, "fov_deg": 360, "detect_air": True, "detect_ground": True, "detect_maritime": False},
    "SIGINT": {"range_nm": 30, "fov_deg": 360, "detect_air": True, "detect_ground": True, "detect_maritime": True},
    "ELINT": {"range_nm": 40, "fov_deg": 360, "detect_air": True, "detect_ground": True, "detect_maritime": True},
    "COMINT": {"range_nm": 25, "fov_deg": 360, "detect_air": True, "detect_ground": True, "detect_maritime": True},
    "RWR": {"range_nm": 50, "fov_deg": 360, "detect_air": True, "detect_ground": False, "detect_maritime": False},
    "AIS": {"range_nm": 40, "fov_deg": 360, "detect_air": False, "detect_ground": False, "detect_maritime": True},
    "SEISMIC": {"range_nm": 5, "fov_deg": 360, "detect_air": False, "detect_ground": True, "detect_maritime": False},
    "MAGNETIC": {"range_nm": 3, "fov_deg": 360, "detect_air": False, "detect_ground": True, "detect_maritime": True},
    "CBRN": {"range_nm": 1, "fov_deg": 360, "detect_air": True, "detect_ground": True, "detect_maritime": False},
    "DIRECTION_FINDING": {"range_nm": 35, "fov_deg": 360, "detect_air": True, "detect_ground": True, "detect_maritime": True},
    "EW_JAMMER": {"range_nm": 20, "fov_deg": 120, "detect_air": True, "detect_ground": True, "detect_maritime": True},
    "GPS": {"range_nm": 0, "fov_deg": 0, "detect_air": False, "detect_ground": False, "detect_maritime": False},
}

NM_TO_DEG = 1.0 / 60.0

SENSOR_MODEL_KEYS = set(SENSOR_COVERAGE)

_SENSOR_NAME_MAP: dict[str, str] = {
    "\u0041\u0045\u0053\u0041 \u96f7\u8fbe": "AESA_RADAR",
    "\u0041\u0045\u0053\u0041\u96f7\u8fbe": "AESA_RADAR",
    "\u0041\u0045\u0057 \u96f7\u8fbe": "AEW_RADAR",
    "\u0041\u0045\u0057\u96f7\u8fbe": "AEW_RADAR",
    "\u5149\u7535": "EO/IR",
    "\u7ea2\u5916": "EO/IR",
    "EO/IR": "EO/IR",
    "EO": "EO/IR",
    "\u5408\u6210\u5b54\u5f84\u96f7\u8fbe": "SAR",
    "\u6fc0\u5149\u96f7\u8fbe": "LIDAR",
    "\u96f7\u8fbe": "RADAR",
    "\u58f0\u7eb3": "SONAR",
    "\u58f0\u5450": "SONAR",
    "\u58f0\u5b66": "ACOUSTIC",
    "\u4fe1\u53f7\u60c5\u62a5": "SIGINT",
    "\u7535\u5b50\u60c5\u62a5": "ELINT",
    "\u901a\u4fe1\u60c5\u62a5": "COMINT",
    "\u96f7\u8fbe\u544a\u8b66": "RWR",
    "\u8239\u8236\u8bc6\u522b": "AIS",
    "\u5730\u9707": "SEISMIC",
    "\u78c1\u529b": "MAGNETIC",
    "\u6838\u751f\u5316": "CBRN",
    "\u6d4b\u5411": "DIRECTION_FINDING",
    "\u7535\u5b50\u5e72\u6270": "EW_JAMMER",
    "GPS": "GPS",
    "ESM": "ELINT",
    "NAV-RADAR": "NAV_RADAR",
    "GROUND-RADAR": "GROUND_RADAR",
    "IRST": "EO/IR",
    "ORBITAL-SAR": "ORBITAL_SAR",
    "HHQ-9": "AESA_RADAR",
}


def normalize_sensor_name(sensor_name: str) -> str:
    """Normalize a scenario sensor name to a sensor model key."""
    if sensor_name in SENSOR_MODEL_KEYS:
        return sensor_name

    mapped = _SENSOR_NAME_MAP.get(sensor_name)
    if mapped and mapped in SENSOR_MODEL_KEYS:
        return mapped

    normalized = sensor_name.replace(" ", "_").upper()
    if normalized in SENSOR_MODEL_KEYS:
        return normalized

    return sensor_name


def distance_nm(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in nautical miles for map/sensor calculations."""
    radius_nm = 3440.065
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2.0) ** 2
    )
    return 2.0 * radius_nm * math.asin(min(1.0, math.sqrt(a)))


def slant_range_nm(
    ground_range_nm: float,
    observer_alt_ft: float = 0.0,
    target_alt_ft: float = 0.0,
) -> float:
    """Return line-of-sight range from ground range and vertical separation."""
    vertical_nm = (float(target_alt_ft) - float(observer_alt_ft)) / 6076.11549
    return math.hypot(float(ground_range_nm), vertical_nm)


def elevation_angle_deg(
    ground_range_nm: float,
    observer_alt_ft: float = 0.0,
    target_alt_ft: float = 0.0,
) -> float:
    """Return target elevation from the observer horizon; depression is negative."""
    horizontal_ft = max(1e-6, float(ground_range_nm) * 6076.11549)
    return math.degrees(
        math.atan2(float(target_alt_ft) - float(observer_alt_ft), horizontal_ft),
    )


def bearing_deg(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Local course in degrees, clockwise from true north."""
    mean_lat = math.radians((lat1 + lat2) / 2.0)
    north = lat2 - lat1
    east = (lng2 - lng1) * math.cos(mean_lat)
    return (math.degrees(math.atan2(east, north)) + 360.0) % 360.0


def offset_position(lat: float, lng: float, bearing: float, range_nm: float) -> tuple[float, float]:
    """Project a short sensor polar observation onto the local map."""
    angle = math.radians(bearing)
    dlat = range_nm / 60.0 * math.cos(angle)
    lon_scale = max(0.2, math.cos(math.radians(lat)))
    dlng = range_nm / (60.0 * lon_scale) * math.sin(angle)
    return lat + dlat, lng + dlng


__all__ = [
    "NM_TO_DEG",
    "SENSOR_COVERAGE",
    "SENSOR_MODEL_KEYS",
    "bearing_deg",
    "distance_nm",
    "elevation_angle_deg",
    "normalize_sensor_name",
    "offset_position",
    "slant_range_nm",
]
