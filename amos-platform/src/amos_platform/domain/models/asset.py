"""Own-force asset and weapon domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WeaponSpec:
    """Weapon performance parameters used by simulation weapon actions."""

    weapon_id: str
    weapon_type: str
    category: str
    range_nm: float
    speed_kts: float
    p_kill: float = 0.7
    warhead: str = "HE"
    guidance: str = "active_radar"

    def to_dict(self) -> dict[str, Any]:
        return {
            "weapon_id": self.weapon_id,
            "weapon_type": self.weapon_type,
            "category": self.category,
            "range_nm": self.range_nm,
            "speed_kts": self.speed_kts,
            "p_kill": self.p_kill,
            "warhead": self.warhead,
            "guidance": self.guidance,
        }


@dataclass
class AssetSnapshot:
    """Own-force asset state."""

    asset_id: str
    domain: str
    role: str
    status: str
    lat: float
    lng: float
    alt_ft: float = 0.0
    heading: float = 0.0
    speed_kts: float = 0.0
    sensors: list[str] = field(default_factory=list)
    weapons: list[str] = field(default_factory=list)
    autonomy_tier: int = 2
    endurance_hr: float = 0.0
    health: dict[str, Any] = field(default_factory=dict)
    member_count: int = 1
    formation_role: str = ""
    network_role: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "asset_id": self.asset_id,
            "domain": self.domain,
            "role": self.role,
            "status": self.status,
            "position": {"lat": self.lat, "lng": self.lng, "alt_ft": self.alt_ft},
            "heading": self.heading,
            "speed_kts": self.speed_kts,
            "sensors": list(self.sensors),
            "weapons": list(self.weapons),
        }
        if self.autonomy_tier > 0:
            result["autonomy_tier"] = self.autonomy_tier
        if self.endurance_hr > 0:
            result["endurance_hr"] = self.endurance_hr
        if self.health:
            result["health"] = dict(self.health)
        if self.member_count > 1:
            result["member_count"] = int(self.member_count)
        if self.formation_role:
            result["formation_role"] = self.formation_role
        if self.network_role:
            result["network_role"] = self.network_role
        return result


__all__ = ["AssetSnapshot", "WeaponSpec"]
