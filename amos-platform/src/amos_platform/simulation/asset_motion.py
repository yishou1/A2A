"""Asset motion helpers for simulation."""

from __future__ import annotations

import math
import time


class WaypointNav:
    def __init__(self):
        self.routes: dict[str, list[dict]] = {}
        self.route_modes: dict[str, str] = {}

    def set_route(self, asset_id: str, waypoints: list[dict], *, mode: str = "hold"):
        """Assign an internal route.

        ``hold`` keeps the asset at the final waypoint. ``loop`` cycles the
        route, which is suitable for aircraft search patterns and holding
        orbits.  Keeping an exhausted hold route registered is intentional:
        it prevents a completed route from falling through to generic
        dead-reckoning motion.
        """
        if mode not in {"hold", "loop"}:
            raise ValueError("route mode must be hold or loop")
        self.routes[asset_id] = list(waypoints)
        self.route_modes[asset_id] = mode

    def add_waypoint(self, asset_id: str, lat: float, lng: float, label: str = ""):
        if asset_id not in self.routes:
            self.routes[asset_id] = []
            self.route_modes[asset_id] = "hold"
        n = len(self.routes[asset_id]) + 1
        self.routes[asset_id].append({
            "lat": lat, "lng": lng,
            "label": label or f"WP-{n}",
        })

    def clear(self, asset_id: str):
        self.routes.pop(asset_id, None)
        self.route_modes.pop(asset_id, None)

    def clear_all(self):
        self.routes.clear()
        self.route_modes.clear()

    def insert_waypoint(self, asset_id: str, index: int,
                        lat: float, lng: float, label: str = "") -> dict:
        """Insert a waypoint at a specific index in an asset's route.

        Args:
            asset_id: The asset whose route to modify.
            index: Insertion position (0 = front, -1 = before last).
            lat, lng: Waypoint coordinates.
            label: Optional waypoint label.

        Returns:
            The inserted waypoint dict.
        """
        if asset_id not in self.routes:
            self.routes[asset_id] = []
            self.route_modes[asset_id] = "hold"
        n = len(self.routes[asset_id]) + 1
        wp = {"lat": lat, "lng": lng, "label": label or f"WP-{n}"}
        self.routes[asset_id].insert(index, wp)
        return wp

    def remove_waypoint(self, asset_id: str, index: int) -> dict | None:
        """Remove a waypoint at a specific index.

        Returns:
            The removed waypoint dict, or None if the asset has no route.
        """
        if asset_id not in self.routes:
            return None
        route = self.routes[asset_id]
        if 0 <= index < len(route):
            return route.pop(index)
        return None

    def update_waypoint(self, asset_id: str, index: int,
                        lat: float | None = None, lng: float | None = None,
                        label: str | None = None) -> dict | None:
        """Update coordinates or label of an existing waypoint.

        Only the provided (non-None) fields are updated.

        Returns:
            The updated waypoint dict, or None if the asset/waypoint not found.
        """
        if asset_id not in self.routes:
            return None
        route = self.routes[asset_id]
        if not (0 <= index < len(route)):
            return None
        wp = route[index]
        if lat is not None:
            wp["lat"] = lat
        if lng is not None:
            wp["lng"] = lng
        if label is not None:
            wp["label"] = label
        return wp

    def get_route(self, asset_id: str) -> list[dict]:
        return list(self.routes.get(asset_id, []))

    def get_all(self) -> dict[str, list[dict]]:
        return {k: list(v) for k, v in self.routes.items()}

    def get_mode(self, asset_id: str) -> str | None:
        return self.route_modes.get(asset_id)

    @staticmethod
    def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        r_nm = 3440.065
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(dlng / 2) ** 2)
        return 2 * r_nm * math.asin(min(1, math.sqrt(a)))

    @staticmethod
    def _bearing(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        mean_lat = math.radians((lat1 + lat2) / 2.0)
        north = lat2 - lat1
        east = (lng2 - lng1) * math.cos(mean_lat)
        return (math.degrees(math.atan2(east, north)) + 360.0) % 360.0

    @staticmethod
    def _turn_toward(current: float, desired: float, max_turn: float) -> float:
        delta = (desired - current + 180.0) % 360.0 - 180.0
        return (current + max(-max_turn, min(max_turn, delta))) % 360.0

    def tick(self, assets: dict, dt: float) -> list[dict]:
        """Advance assets toward their waypoints. Returns reached-waypoint events."""
        events = []
        for asset_id, wps in list(self.routes.items()):
            if not wps or asset_id not in assets:
                continue
            asset = assets[asset_id]
            pos = asset.get("position", asset)
            speed_kts = max(0.0, float(asset.get("speed_kts", 30)))
            domain = str(asset.get("domain") or "").lower()
            default_turn_rate = 4.0 if domain == "air" else (0.8 if domain == "maritime" else 0.2)
            max_turn_rate = float(asset.get("max_turn_rate_dps", default_turn_rate))
            remaining_sec = max(0.0, float(dt))
            zero_time_hops = 0
            while remaining_sec > 1e-9 and wps and speed_kts > 0:
                target = wps[0]
                lat1, lng1 = float(pos.get("lat", 0)), float(pos.get("lng", 0))
                lat2, lng2 = float(target["lat"]), float(target["lng"])
                dist = self._haversine(lat1, lng1, lat2, lng2)
                desired = self._bearing(lat1, lng1, lat2, lng2)
                sub_dt = min(1.0, remaining_sec)
                current = float(asset.get("heading_deg", desired))
                heading = self._turn_toward(
                    current,
                    desired,
                    max(0.0, max_turn_rate * sub_dt),
                )
                move_nm = speed_kts * sub_dt / 3600.0
                heading_error = abs((heading - desired + 180.0) % 360.0 - 180.0)
                if dist <= 0.01 or (dist <= move_nm and heading_error <= max(1.0, max_turn_rate * sub_dt)):
                    travel_sec = min(sub_dt, dist / speed_kts * 3600.0) if dist > 0 else 0.0
                    reached = wps.pop(0)
                    pos["lat"] = round(float(reached["lat"]), 6)
                    pos["lng"] = round(float(reached["lng"]), 6)
                    asset["heading_deg"] = round(desired, 1)
                    remaining_sec -= travel_sec
                    zero_time_hops = zero_time_hops + 1 if travel_sec <= 1e-9 else 0
                    events.append({
                        "type": "waypoint_reached",
                        "asset_id": asset_id,
                        "waypoint": reached,
                        "time": time.time(),
                    })
                    if self.route_modes.get(asset_id) == "loop":
                        wps.append(reached)
                        # A malformed loop containing only coincident points
                        # must not spin forever without consuming simulation
                        # time. Formal scenarios never rely on such a route.
                        if zero_time_hops >= max(1, len(wps)):
                            remaining_sec -= sub_dt
                            break
                    elif not wps:
                        asset["speed_kts"] = 0.0
                        if str(asset.get("status") or "").casefold() in {"active", "operational"}:
                            asset["status"] = "holding"
                        break
                    continue

                asset["heading_deg"] = round(heading, 1)
                heading_rad = math.radians(heading)
                dlat = move_nm / 60.0 * math.cos(heading_rad)
                lon_scale = max(0.2, math.cos(math.radians(lat1)))
                dlng = move_nm / (60.0 * lon_scale) * math.sin(heading_rad)
                pos["lat"] = round(lat1 + dlat, 6)
                pos["lng"] = round(lng1 + dlng, 6)
                remaining_sec -= sub_dt
                zero_time_hops = 0

        return events


__all__ = ["WaypointNav"]
