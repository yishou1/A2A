"""Platform-internal sensor observation simulator.

This module converts internal truth into agent-visible observations. Truth
associations are returned separately for admin/evaluator use only.
"""

from __future__ import annotations

import math
import random
import uuid
from typing import Any

from amos_platform.domain.models.observation import ObservationBatch, SensorObservation
from amos_platform.domain.models.perception import TruthAssociation
from amos_platform.domain.policies.visibility import sanitize_asset
from amos_platform.sensors.coverage import (
    SENSOR_COVERAGE,
    bearing_deg,
    distance_nm,
    elevation_angle_deg,
    normalize_sensor_name,
    slant_range_nm,
)


def _entity_position(entity: dict[str, Any]) -> dict[str, float]:
    pos = entity.get("position") if isinstance(entity.get("position"), dict) else entity
    return {
        "lat": float(pos.get("lat", 0.0)),
        "lng": float(pos.get("lng", pos.get("lon", 0.0))),
        "alt_ft": float(pos.get("alt_ft", pos.get("altitude_ft", 0.0)) or 0.0),
    }


def _angle_delta(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _domain_detectable(domain: str, spec: dict[str, Any]) -> bool:
    if domain == "air":
        return bool(spec.get("detect_air"))
    if domain == "ground":
        return bool(spec.get("detect_ground"))
    return bool(spec.get("detect_maritime"))


class SensorSimulator:
    """Generate agent-visible sensor observations from internal world state."""

    def __init__(
        self,
        *,
        detection_probability: float = 1.0,
        false_alarm_probability: float = 0.0,
        rng: random.Random | None = None,
    ):
        self.detection_probability = detection_probability
        self.false_alarm_probability = false_alarm_probability
        self._rng = rng or random.Random()

    def _uuid_hex(self) -> str:
        return uuid.UUID(int=self._rng.getrandbits(128)).hex

    def generate_observations(
        self,
        assets: dict[str, dict[str, Any]],
        threats: dict[str, dict[str, Any]],
        *,
        sim_time: float = 0.0,
        tick_id: int = 0,
        run_id: str = "",
        scenario_id: str = "",
    ) -> dict[str, Any]:
        observations: list[dict[str, Any]] = []
        media_items: list[dict[str, Any]] = []
        truth_associations: list[dict[str, Any]] = []

        for asset_id, asset in assets.items():
            if asset.get("status") not in ("operational", "active", "holding"):
                continue
            apos = _entity_position(asset)
            heading = float(asset.get("heading_deg", asset.get("heading", 0.0)))

            for sensor_index, sensor_name in enumerate(asset.get("sensors") or []):
                sensor_norm = normalize_sensor_name(str(sensor_name))
                spec = SENSOR_COVERAGE.get(sensor_norm, {})
                if not spec or spec.get("range_nm", 0) == 0:
                    continue

                for truth_index, (truth_id, threat) in enumerate(threats.items()):
                    if threat.get("neutralized"):
                        continue
                    observation_window = threat.get("_observation_window") or {}
                    observable_from = float(observation_window.get("start_sec", 0) or 0)
                    observable_until = observation_window.get("end_sec")
                    if sim_time < observable_from or (
                        observable_until is not None and sim_time >= float(observable_until)
                    ):
                        continue
                    if self.detection_probability <= 0:
                        continue
                    domain = str(threat.get("domain", "maritime"))
                    if not _domain_detectable(domain, spec):
                        continue
                    if sensor_norm == "AIS" and not threat.get("ais_match"):
                        continue
                    if sensor_norm in {"ELINT", "SIGINT", "COMINT", "DIRECTION_FINDING"} and threat.get("rf_freq_mhz") is None:
                        continue

                    tpos = _entity_position(threat)
                    ground_range_nm = distance_nm(
                        apos["lat"], apos["lng"], tpos["lat"], tpos["lng"],
                    )
                    line_of_sight_nm = slant_range_nm(
                        ground_range_nm,
                        apos["alt_ft"],
                        tpos["alt_ft"],
                    )
                    if line_of_sight_nm > float(spec.get("range_nm", 0.0)):
                        continue

                    bearing = bearing_deg(
                        apos["lat"], apos["lng"], tpos["lat"], tpos["lng"],
                    )
                    fov = float(spec.get("fov_deg", 360.0))
                    if fov < 360.0 and _angle_delta(bearing, heading) > fov / 2.0:
                        continue

                    observation_id = f"OBS-{tick_id}-{self._uuid_hex()[:10]}"
                    confidence = max(0.1, min(0.99, 1.0 - (line_of_sight_nm / max(float(spec["range_nm"]), 1.0)) * 0.5))
                    modality = sensor_norm or str(sensor_name)
                    observation = SensorObservation(
                        observation_id=observation_id,
                        sensor_id=str(sensor_name),
                        asset_id=str(asset_id),
                        sim_time=sim_time,
                        modality=modality,
                        bearing_deg=round(bearing, 2),
                        range_nm=round(ground_range_nm, 2),
                        slant_range_nm=round(line_of_sight_nm, 2),
                        elevation_deg=round(elevation_angle_deg(
                            ground_range_nm,
                            apos["alt_ft"],
                            tpos["alt_ft"],
                        ), 2),
                        domain_hint=domain,
                        range_rate_kts=float(threat.get("speed_kts", 0.0)) - float(asset.get("speed_kts", 0.0)),
                        snr_db=round(30.0 * confidence, 2),
                        covariance={
                            "bearing_deg": round(max(0.5, 5.0 * (1.0 - confidence)), 2),
                            "range_nm": round(max(0.1, ground_range_nm * 0.03), 2),
                        },
                        confidence=round(confidence, 2),
                        quality="nominal" if confidence >= 0.5 else "low",
                    ).to_dict()
                    if sensor_norm in {"ELINT", "SIGINT", "COMINT", "DIRECTION_FINDING"}:
                        # RF products represent receiver measurements rather
                        # than copying transmitter truth verbatim. Seeded
                        # measurement jitter produces a reproducible waterfall
                        # while range loss keeps received power physically
                        # ordered across platforms.
                        observation["frequency_mhz"] = round(
                            float(threat["rf_freq_mhz"]) + self._rng.gauss(0.0, 0.08),
                            3,
                        )
                        if threat.get("power_dbm") is not None:
                            observation["power_dbm"] = round(
                                float(threat["power_dbm"])
                                - 0.25 * line_of_sight_nm
                                + self._rng.gauss(0.0, 1.2),
                                2,
                            )
                    observations.append(observation)

                    truth_associations.append(TruthAssociation(
                        truth_id=str(truth_id),
                        observation_id=observation_id,
                        association_type="sensor_detection",
                        confidence=round(confidence, 2),
                    ).to_dict())

        batch = ObservationBatch(
            batch_id=f"BATCH-{tick_id}-{self._uuid_hex()[:8]}",
            run_id=run_id,
            scenario_id=scenario_id,
            tick_id=tick_id,
            sim_time=sim_time,
            observer_scope={"type": "own_force"},
            own_asset_poses=[sanitize_asset(asset) for asset in assets.values()],
            observations=observations,
            media_refs=media_items,
        ).to_dict()
        return {
            "batch": batch,
            "truth_associations": truth_associations,
        }


__all__ = ["SensorSimulator"]
