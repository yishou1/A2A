"""Geofence Manager — boundary-based alerting for missions."""

import time
import uuid


class GeofenceManager:
    def __init__(self):
        self.geofences: dict[str, dict] = {}
        self.alerts: list[dict] = []
        self._inside: set[tuple[str, str, str]] = set()

    def add(self, name: str, polygon: list[dict], gtype: str = "operational") -> str:
        """Add a geofence polygon. Returns geofence ID."""
        gf_id = f"GF-{uuid.uuid4().hex[:8]}"
        self.geofences[gf_id] = {
            "id": gf_id,
            "name": name,
            "type": gtype,
            "polygon": polygon,
            "created": time.time(),
            "active": True,
        }
        return gf_id

    def remove(self, gf_id: str):
        self.geofences.pop(gf_id, None)
        self._inside = {entry for entry in self._inside if entry[0] != gf_id}

    def get_all(self) -> dict:
        return dict(self.geofences)

    def get_alerts(self, limit: int = 20) -> list[dict]:
        return self.alerts[-limit:]

    def _point_in_polygon(self, lat: float, lng: float, polygon: list[dict]) -> bool:
        """Ray-casting algorithm."""
        n = len(polygon)
        inside = False
        j = n - 1
        for i in range(n):
            yi = polygon[i].get("lat", polygon[i].get("y", 0))
            xi = polygon[i].get("lng", polygon[i].get("x", 0))
            yj = polygon[j].get("lat", polygon[j].get("y", 0))
            xj = polygon[j].get("lng", polygon[j].get("x", 0))
            if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi):
                inside = not inside
            j = i
        return inside

    def tick(self, assets: dict, contacts: dict) -> list[dict]:
        """Emit entry events for own assets and sensor-derived contacts."""
        events = []
        inside_now: set[tuple[str, str, str]] = set()
        for gf_id, gf in self.geofences.items():
            if not gf.get("active"):
                continue
            poly = gf.get("polygon", [])
            if not poly:
                continue

            # Check assets
            for aid, a in assets.items():
                if a.get("status") not in ("operational", "active"):
                    continue
                pos = a.get("position", a)
                lat, lng = pos.get("lat", 0), pos.get("lng", 0)
                if self._point_in_polygon(lat, lng, poly):
                    entry = (gf_id, "asset", aid)
                    inside_now.add(entry)
                    if entry in self._inside:
                        continue
                    ev = {
                        "type": "geofence",
                        "entity_id": aid,
                        "entity_type": "asset",
                        "geofence_id": gf_id,
                        "geofence_name": gf["name"],
                        "event": "inside",
                        "lat": lat, "lng": lng,
                        "timestamp": time.time(),
                    }
                    events.append(ev)
                    self.alerts.append(ev)

            # Check only fused contacts. Ground-truth scenario entities are not
            # a valid source for operator-visible geofence alerts.
            for contact_id, contact in contacts.items():
                lat, lng = contact.get("lat", 0), contact.get("lng", contact.get("lon", 0))
                if self._point_in_polygon(lat, lng, poly):
                    entry = (gf_id, "contact", contact_id)
                    inside_now.add(entry)
                    if entry in self._inside:
                        continue
                    ev = {
                        "type": "geofence",
                        "entity_id": contact_id,
                        "entity_type": "contact",
                        "geofence_id": gf_id,
                        "geofence_name": gf["name"],
                        "event": "inside",
                        "lat": lat, "lng": lng,
                        "timestamp": time.time(),
                    }
                    events.append(ev)
                    self.alerts.append(ev)

        # Entries disappear from the set after leaving, allowing a later
        # re-entry to create a fresh event without flooding every tick.
        self._inside = inside_now

        # Prune old alerts
        if len(self.alerts) > 200:
            self.alerts = self.alerts[-100:]

        return events


__all__ = ["GeofenceManager"]
