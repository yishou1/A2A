"""AIS/ADS-B CSV adapters for track and threat analysis inputs."""

from __future__ import annotations

import csv
import math

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from decision_agents.schemas import Observation


SUPPORTED_SOURCE_FORMATS = {"ais_ushant", "adsb_opensky"}
MPS_TO_KNOTS = 1.9438444924406
NAUTICAL_MILES_PER_DEGREE = 60.0


@dataclass
class DatasetLoadResult:
    observations: list[Observation]
    warnings: list[str] = field(default_factory=list)
    source_format: str = ""
    skipped_rows: int = 0


def load_observations_from_csv(
    source_format: str,
    path: str | Path,
    limit: int | None = None,
) -> list[Observation]:
    """Load AIS/ADS-B CSV rows into normalized observations."""
    return load_observation_result_from_csv(source_format, path, limit).observations


def load_observation_result_from_csv(
    source_format: str,
    path: str | Path,
    limit: int | None = None,
) -> DatasetLoadResult:
    """Load AIS/ADS-B CSV rows and include row-level warnings."""
    if source_format not in SUPPORTED_SOURCE_FORMATS:
        supported = ", ".join(sorted(SUPPORTED_SOURCE_FORMATS))
        raise ValueError(f"unsupported source_format '{source_format}', expected one of: {supported}")

    csv_path = Path(path)
    rows = _read_rows(csv_path)
    valid_records: list[dict[str, Any]] = []
    warnings = []
    skipped_rows = 0

    for row_number, row in enumerate(rows, start=2):
        try:
            record = _extract_record(source_format, row)
        except ValueError as exc:
            warnings.append(f"row {row_number}: {exc}")
            skipped_rows += 1
            continue
        valid_records.append(record)
        if limit is not None and len(valid_records) >= limit:
            break

    if not valid_records:
        return DatasetLoadResult(
            observations=[],
            warnings=warnings,
            source_format=source_format,
            skipped_rows=skipped_rows,
        )

    origin_lat = valid_records[0]["latitude"]
    origin_lon = valid_records[0]["longitude"]
    first_time = valid_records[0]["time_value"]
    observations = [
        _build_observation(
            source_format=source_format,
            record=record,
            index=index,
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            first_time=first_time,
        )
        for index, record in enumerate(valid_records, start=1)
    ]
    return DatasetLoadResult(
        observations=observations,
        warnings=warnings,
        source_format=source_format,
        skipped_rows=skipped_rows,
    )


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        return [dict(row) for row in reader]


def _extract_record(source_format: str, row: dict[str, str]) -> dict[str, Any]:
    normalized = {_normalize_key(key): value for key, value in row.items()}
    if source_format == "ais_ushant":
        return _extract_ais_record(normalized, row)
    return _extract_adsb_record(normalized, row)


def _extract_ais_record(normalized: dict[str, str], raw_row: dict[str, str]) -> dict[str, Any]:
    target = _first_text(normalized, ("mmsi", "shipid", "trajid", "vesselid"))
    latitude = _required_float(normalized, ("lat", "latitude"), "latitude")
    longitude = _required_float(normalized, ("lon", "long", "longitude"), "longitude")
    time_value, time_text = _required_time(
        normalized,
        ("timestamp", "time", "datetime", "basedatetime"),
    )
    if not target:
        raise ValueError("missing target id (MMSI/ship_id/traj_id)")
    speed = _optional_float(normalized, ("sog", "speed", "speedoverground"))
    heading = _optional_float(normalized, ("cog", "heading", "trueheading"))
    object_type = _first_text(normalized, ("vesseltype", "shiptype", "objecttype"))

    return {
        "target": target,
        "latitude": latitude,
        "longitude": longitude,
        "time_value": time_value,
        "time_text": time_text,
        "speed_hint": speed,
        "heading_hint": _normalize_heading(heading),
        "altitude": None,
        "object_type": _normalize_object_type(object_type, default="surface_contact"),
        "sensor_id": _first_text(normalized, ("sensorid", "receiverid", "stationid")),
        "confidence": _optional_float(normalized, ("confidence",)),
        "source_reliability": _optional_float(normalized, ("sourcereliability", "reliability")),
        "raw": raw_row,
    }


def _extract_adsb_record(normalized: dict[str, str], raw_row: dict[str, str]) -> dict[str, Any]:
    target = _first_text(normalized, ("icao24", "callsign"))
    latitude = _required_float(normalized, ("lat", "latitude"), "latitude")
    longitude = _required_float(normalized, ("lon", "long", "longitude"), "longitude")
    time_value, time_text = _required_time(
        normalized,
        ("timestamp", "time", "lastcontact", "timeposition", "datetime"),
    )
    if not target:
        raise ValueError("missing target id (icao24/callsign)")
    speed_mps = _optional_float(normalized, ("velocity",))
    speed_knots = _optional_float(normalized, ("groundspeed", "speed", "speedknots"))
    speed = speed_knots if speed_knots is not None else _to_knots(speed_mps)
    heading = _optional_float(normalized, ("heading", "truetrack", "track"))
    altitude = _optional_float(normalized, ("geoaltitude", "baroaltitude", "altitude"))

    return {
        "target": target,
        "latitude": latitude,
        "longitude": longitude,
        "time_value": time_value,
        "time_text": time_text,
        "speed_hint": speed,
        "heading_hint": _normalize_heading(heading),
        "altitude": altitude,
        "object_type": "air_track",
        "sensor_id": _first_text(normalized, ("sensorid", "receiverid")),
        "confidence": _optional_float(normalized, ("confidence",)),
        "source_reliability": _optional_float(normalized, ("sourcereliability", "reliability")),
        "raw": raw_row,
    }


def _build_observation(
    source_format: str,
    record: dict[str, Any],
    index: int,
    origin_lat: float,
    origin_lon: float,
    first_time: float,
) -> Observation:
    x, y = _project_to_nautical_miles(
        record["latitude"],
        record["longitude"],
        origin_lat,
        origin_lon,
    )
    hours = (record["time_value"] - first_time) / 3600.0
    target = str(record["target"]).strip()
    return Observation(
        id=f"{source_format.upper()}-{index:05d}",
        timestamp=f"T+{hours:.4f}",
        x=round(x, 4),
        y=round(y, 4),
        latitude=record["latitude"],
        longitude=record["longitude"],
        altitude=record["altitude"],
        source_format=source_format,
        confidence=_bounded(record["confidence"], default=0.85),
        target_hint=target,
        sensor_id=record["sensor_id"],
        source_reliability=_bounded(record["source_reliability"], default=1.0),
        object_type=record["object_type"],
        speed_hint=record["speed_hint"],
        heading_hint=record["heading_hint"],
        features={
            "raw": record["raw"],
            "dataset_source": source_format,
            "original_timestamp": record["time_text"],
        },
    )


def _project_to_nautical_miles(
    latitude: float,
    longitude: float,
    origin_lat: float,
    origin_lon: float,
) -> tuple[float, float]:
    mean_lat = math.radians((latitude + origin_lat) / 2.0)
    x = (longitude - origin_lon) * math.cos(mean_lat) * NAUTICAL_MILES_PER_DEGREE
    y = (latitude - origin_lat) * NAUTICAL_MILES_PER_DEGREE
    return x, y


def _required_float(
    normalized: dict[str, str],
    keys: tuple[str, ...],
    label: str,
) -> float:
    value = _optional_float(normalized, keys)
    if value is None:
        raise ValueError(f"missing or invalid {label}")
    return value


def _optional_float(normalized: dict[str, str], keys: tuple[str, ...]) -> float | None:
    value = _first_text(normalized, keys)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _required_time(
    normalized: dict[str, str],
    keys: tuple[str, ...],
) -> tuple[float, str]:
    value = _first_text(normalized, keys)
    if value is None:
        raise ValueError("missing timestamp")
    parsed = _parse_time(value)
    if parsed is None:
        raise ValueError("invalid timestamp")
    return parsed, value


def _parse_time(value: str) -> float | None:
    stripped = value.strip()
    try:
        return float(stripped)
    except ValueError:
        pass

    candidate = stripped.replace("Z", "+00:00")
    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    )
    for fmt in formats:
        try:
            dt = datetime.strptime(stripped, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _first_text(normalized: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = normalized.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _normalize_key(value: str | None) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _normalize_heading(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value % 360.0, 3)


def _normalize_object_type(value: str | None, default: str) -> str:
    if not value:
        return default
    normalized = "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip())
    normalized = "_".join(part for part in normalized.split("_") if part)
    return normalized or default


def _to_knots(value_mps: float | None) -> float | None:
    if value_mps is None:
        return None
    return round(value_mps * MPS_TO_KNOTS, 3)


def _bounded(value: float | None, default: float) -> float:
    if value is None:
        return default
    return max(0.0, min(float(value), 1.0))
