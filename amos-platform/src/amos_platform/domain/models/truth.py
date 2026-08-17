"""Truth/internal target domain models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ThreatSnapshot:
    """Truth-owned threat / target state."""

    threat_id: str
    threat_type: str
    domain: str
    lat: float
    lng: float
    heading: float = 0.0
    speed_kts: float = 0.0
    status: str = "active"
    risk_level: str = "UNKNOWN"
    rf_freq_mhz: float | None = None
    power_dbm: float | None = None
    rcs_dbsm: float | None = None
    ir_signature: str = "medium"
    iff_status: str = "unknown"
    ais_match: bool = False
    behavior_script: dict | None = None
    alt_ft: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "threat_id": self.threat_id,
            "threat_type": self.threat_type,
            "domain": self.domain,
            "position": {"lat": self.lat, "lng": self.lng, "alt_ft": self.alt_ft},
            "heading": self.heading,
            "speed_kts": self.speed_kts,
            "status": self.status,
            "risk_level": self.risk_level,
        }
        if self.rf_freq_mhz is not None:
            result["rf_freq_mhz"] = self.rf_freq_mhz
        if self.power_dbm is not None:
            result["power_dbm"] = self.power_dbm
        if self.rcs_dbsm is not None:
            result["rcs_dbsm"] = self.rcs_dbsm
        if self.ir_signature != "medium":
            result["ir_signature"] = self.ir_signature
        if self.iff_status != "unknown":
            result["iff_status"] = self.iff_status
        if self.ais_match:
            result["ais_match"] = self.ais_match
        if self.behavior_script is not None:
            result["behavior_script"] = self.behavior_script
        return result


__all__ = ["ThreatSnapshot"]
