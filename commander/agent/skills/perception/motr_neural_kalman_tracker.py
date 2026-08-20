"""MOTR + Neural Kalman Filter：端到端多目标跟踪与定位。"""

from __future__ import annotations

import math
from typing import Any

from agent.skills.base import AlgorithmBackend


class MOTRNeuralKalmanTracker(AlgorithmBackend[dict[str, Any]]):
    name = "MOTR+Neural-Kalman"

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        verified = inputs.get("verified_detections", [])
        prior_tracks = inputs.get("prior_tracks", [])
        if self.use_mock:
            return self._mock_track(verified, prior_tracks, inputs)
        return self._infer(verified, prior_tracks, inputs)

    def _mock_track(
        self,
        verified: list[dict[str, Any]],
        prior_tracks: list[dict[str, Any]],
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        from agent.inference.geo_estimator import estimate_target_geo
        from agent.inference.geolocation import parse_sensor_georef_context

        visual_frame = inputs.get("visual_frame")
        batch_context = inputs.get("batch_context")
        frame_dict = visual_frame if isinstance(visual_frame, dict) else None
        frame_meta = (frame_dict or {}).get("metadata") or {}
        georef = parse_sensor_georef_context(
            frame_dict,
            self.config,
            batch_context=batch_context if isinstance(batch_context, dict) else None,
        )
        prior_geo_by_id = {
            str(pt.get("track_id", "")): pt.get("geo")
            for pt in prior_tracks
            if pt.get("track_id") and pt.get("geo")
        }

        keyed_detections: dict[str, dict[str, Any]] = {}
        unkeyed_detections: list[dict[str, Any]] = []
        for det in verified:
            metadata = det.get("metadata") if isinstance(det.get("metadata"), dict) else {}
            provided_id = (
                det.get("track_id")
                or metadata.get("amos_track_id")
                or metadata.get("source_contact_id")
            )
            if provided_id:
                keyed_detections[str(provided_id)] = det
            else:
                unkeyed_detections.append(det)
        detections = [*unkeyed_detections, *keyed_detections.values()]

        tracks: list[dict[str, Any]] = []
        for i, det in enumerate(detections):
            metadata = det.get("metadata") if isinstance(det.get("metadata"), dict) else {}
            provided_id = (
                det.get("track_id")
                or metadata.get("amos_track_id")
                or metadata.get("source_contact_id")
            )
            track_id = str(provided_id or f"T-{len(prior_tracks) + i + 1:04d}")
            bbox = det.get("bbox") or [0, 0, 0, 0]
            cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            prior_geo = prior_geo_by_id.get(track_id)
            provided_geo = det.get("geo") if isinstance(det.get("geo"), dict) else {}
            try:
                provided_lat = float(provided_geo.get("lat"))
                provided_lon = float(provided_geo.get("lon", provided_geo.get("lng")))
                valid_provided_geo = (
                    math.isfinite(provided_lat)
                    and math.isfinite(provided_lon)
                    and -90.0 <= provided_lat <= 90.0
                    and -180.0 <= provided_lon <= 180.0
                )
            except (TypeError, ValueError):
                valid_provided_geo = False
            if valid_provided_geo:
                geo = {
                    **provided_geo,
                    "lat": provided_lat,
                    "lon": provided_lon,
                    "alt_m": float(
                        provided_geo.get("alt_m", provided_geo.get("alt", 0.0)) or 0.0
                    ),
                    "geo_method": "provided_detection_geo",
                    "class_name": str(det.get("class_name", "unknown")),
                }
                domain_hint = metadata.get("domain_hint")
                if domain_hint:
                    geo.setdefault("domain", str(domain_hint))
            else:
                geo = estimate_target_geo(
                    bbox,
                    georef,
                    class_name=str(det.get("class_name", "unknown")),
                    det_meta=metadata,
                    frame_meta=frame_meta,
                    batch_context=batch_context if isinstance(batch_context, dict) else None,
                    config=self.config,
                    prior_geo=prior_geo if isinstance(prior_geo, dict) else None,
                    smooth_alpha=float(self.config.get("geo_smooth_alpha", 0.7)),
                )
            tracks.append(
                {
                    "track_id": track_id,
                    "class_name": det.get("class_name", "unknown"),
                    "confidence": det.get("confidence", 0.0),
                    "state": "active",
                    "bbox": bbox,
                    "last_bbox": bbox,
                    "position_px": [cx, cy],
                    "geo": geo,
                    "kalman_gain": 0.62,
                }
            )
        return {"tracks": tracks, "associations": len(tracks)}

    def _infer(
        self, verified: list[dict[str, Any]], prior_tracks: list[dict[str, Any]], inputs: dict[str, Any]
    ) -> dict[str, Any]:
        from agent.inference.tracking import track_objects

        return track_objects(
            verified,
            prior_tracks,
            self.config,
            visual_frame=inputs.get("visual_frame"),
            batch_context=inputs.get("batch_context"),
        )
