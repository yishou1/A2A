"""World-state support helpers for simulation."""

from __future__ import annotations

import math
import time


FREQUENCY_BANDS = {
    "VHF":    {"range_km": 30,  "bandwidth_mbps": 1,   "jam_resist": 0.3},
    "UHF":    {"range_km": 50,  "bandwidth_mbps": 5,   "jam_resist": 0.5},
    "L_BAND": {"range_km": 80,  "bandwidth_mbps": 10,  "jam_resist": 0.6},
    "S_BAND": {"range_km": 40,  "bandwidth_mbps": 25,  "jam_resist": 0.7},
    "C_BAND": {"range_km": 25,  "bandwidth_mbps": 50,  "jam_resist": 0.8},
    "KU_BAND": {"range_km": 15,  "bandwidth_mbps": 100, "jam_resist": 0.4},
    "SATCOM": {"range_km": 999, "bandwidth_mbps": 20,  "jam_resist": 0.9},
}


class MeshNetwork:
    """Simplified mesh network simulation for simulation platform."""

    def __init__(self, default_band: str = "UHF"):
        self.nodes: dict[str, dict] = {}
        self.links: dict[str, dict] = {}
        self.default_band = default_band
        self.last_tick = time.time()
        self._sim_accumulator = 0.0
        self._history: list[dict] = []
        self._last_history_sec: float | None = None

    def register_node(self, node_id: str, lat: float, lng: float,
                      node_type: str = "asset", band: str | None = None,
                      comm_profile: dict | None = None):
        comm_profile = comm_profile or {}
        self.nodes[node_id] = {
            "id": node_id,
            "type": node_type,
            "lat": lat, "lng": lng,
            "band": str(comm_profile.get("band") or band or self.default_band),
            "range_km": float(comm_profile.get("range_km", 0) or 0) or None,
            "bandwidth_mbps": float(comm_profile.get("bandwidth_mbps", 0) or 0) or None,
            "role": str(comm_profile.get("role") or "mesh_member"),
            "status": "active",
            "comms_strength": 100,
            "connections": [],
        }

    def remove_node(self, node_id: str):
        self.nodes.pop(node_id, None)
        to_remove = [k for k in self.links if node_id in k]
        for k in to_remove:
            self.links.pop(k, None)
        for node in self.nodes.values():
            if node_id in node.get("connections", []):
                node["connections"].remove(node_id)

    def _link_key(self, a: str, b: str) -> str:
        return f"{a}<>" + b if a < b else f"{b}<>" + a

    def _distance_km(self, lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        r_km = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(dlng / 2) ** 2)
        return 2 * r_km * math.asin(min(1, math.sqrt(a)))

    def tick(self, assets: dict, dt: float = 0.0, *, sim_time: float | None = None):
        """Update node positions from assets, compute links, degrade from EW."""
        if dt > 0:
            self._sim_accumulator += float(dt)
            if self._sim_accumulator < 0.5:
                return
            self._sim_accumulator = 0.0
        else:
            now = time.time()
            if now - self.last_tick < 0.5:
                return
            self.last_tick = now

        for aid, asset in assets.items():
            if aid in self.nodes:
                pos = asset.get("position", asset)
                self.nodes[aid]["lat"] = pos.get("lat", 0)
                self.nodes[aid]["lng"] = pos.get("lng", 0)
                health = asset.get("health", {})
                self.nodes[aid]["comms_strength"] = health.get("comms_strength",
                    asset.get("comms_strength", 100))
                status = str(asset.get("status") or "").casefold()
                self.nodes[aid]["status"] = "active" if status in {"active", "operational", "holding"} else status

        # Rebuild the graph from the current tick. This prevents a staged or
        # unavailable platform from retaining a stale link from an earlier
        # state.
        self.links = {}
        for node in self.nodes.values():
            node["connections"] = []

        node_ids = list(self.nodes.keys())
        for i in range(len(node_ids)):
            for j in range(i + 1, len(node_ids)):
                na = self.nodes[node_ids[i]]
                nb = self.nodes[node_ids[j]]
                if na["status"] != "active" or nb["status"] != "active":
                    continue

                dist_km = self._distance_km(
                    na["lat"], na["lng"], nb["lat"], nb["lng"])
                band_a = FREQUENCY_BANDS.get(na["band"], {"range_km": 50, "bandwidth_mbps": 5})
                band_b = FREQUENCY_BANDS.get(nb["band"], {"range_km": 50, "bandwidth_mbps": 5})
                max_range = min(
                    float(na.get("range_km") or band_a["range_km"]),
                    float(nb.get("range_km") or band_b["range_km"]),
                )
                bw = min(
                    float(na.get("bandwidth_mbps") or band_a["bandwidth_mbps"]),
                    float(nb.get("bandwidth_mbps") or band_b["bandwidth_mbps"]),
                )

                key = self._link_key(node_ids[i], node_ids[j])

                if dist_km <= max_range:
                    quality = max(0.1, 1.0 - dist_km / max_range)
                    quality *= min(na["comms_strength"], nb["comms_strength"]) / 100.0
                    rssi_dbm = max(-118.0, -48.0 - 20.0 * math.log10(max(dist_km, 0.1)) - (1.0 - quality) * 12.0)
                    snr_db = max(-5.0, min(38.0, rssi_dbm + 102.0))
                    packet_loss = min(100.0, max(0.0, (1.0 - quality) ** 2 * 28.0))
                    latency_ms = 12.0 + dist_km * 0.08 + (1.0 - quality) * 35.0
                    self.links[key] = {
                        "from": node_ids[i], "to": node_ids[j],
                        "quality": round(quality, 2),
                        "band": na["band"] if na["band"] == nb["band"] else f"{na['band']}/{nb['band']}",
                        "bandwidth_mbps": round(bw * quality, 2),
                        "distance_km": round(dist_km, 1),
                        "rssi_dbm": round(rssi_dbm, 1),
                        "snr_db": round(snr_db, 1),
                        "packet_loss_pct": round(packet_loss, 2),
                        "latency_ms": round(latency_ms, 1),
                        "jitter_ms": round(1.0 + (1.0 - quality) * 9.0, 1),
                    }
                    if node_ids[j] not in na["connections"]:
                        na["connections"].append(node_ids[j])
                    if node_ids[i] not in nb["connections"]:
                        nb["connections"].append(node_ids[i])
                else:
                    self.links.pop(key, None)
                    if node_ids[j] in na["connections"]:
                        na["connections"].remove(node_ids[j])
                    if node_ids[i] in nb["connections"]:
                        nb["connections"].remove(node_ids[i])

        sample_time = float(sim_time) if sim_time is not None else (
            (self._last_history_sec or 0.0) + max(0.0, float(dt))
        )
        if self._last_history_sec is None or sample_time - self._last_history_sec >= 5.0:
            self._history.append({
                "sim_time": round(sample_time, 3),
                "links": [dict(link) for _, link in sorted(self.links.items())],
            })
            self._last_history_sec = sample_time
            cutoff = sample_time - 600.0
            self._history = [
                item for item in self._history[-160:]
                if float(item.get("sim_time", 0) or 0) >= cutoff
            ]

    def get_topology(self) -> dict:
        """Return network topology summary."""
        active_nodes = sum(1 for n in self.nodes.values() if n["status"] == "active")
        active_links = sum(1 for l in self.links.values() if l["quality"] > 0.3)
        degraded_links = sum(1 for l in self.links.values() if l["quality"] <= 0.3)
        avg_quality = (sum(l["quality"] for l in self.links.values()) /
                       max(1, len(self.links))) if self.links else 0

        return {
            "nodes": active_nodes,
            "links": active_links,
            "degraded_links": degraded_links,
            "avg_quality": round(avg_quality, 2),
            "resilience": round(min(100,
                active_links / max(1, active_nodes) * 30 +
                avg_quality * 50 +
                (active_nodes - degraded_links) / max(1, active_nodes) * 20), 1),
            "node_records": [
                {
                    "id": node["id"],
                    "type": node["type"],
                    "status": node["status"],
                    "band": node["band"],
                    "role": node.get("role"),
                    "comms_strength": round(float(node.get("comms_strength", 0) or 0), 1),
                }
                for node in sorted(self.nodes.values(), key=lambda item: item["id"])
            ],
            "link_records": [
                dict(link)
                for _, link in sorted(self.links.items())
            ],
            "history_records": [
                {
                    "sim_time": item["sim_time"],
                    "links": [dict(link) for link in item.get("links") or []],
                }
                for item in self._history
            ],
        }


__all__ = ["FREQUENCY_BANDS", "MeshNetwork"]
