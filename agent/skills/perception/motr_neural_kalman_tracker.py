"""MOTR + Neural Kalman Filter：端到端多目标跟踪与定位。"""

from __future__ import annotations

from typing import Any

from agent.skills.base import AlgorithmBackend


class MOTRNeuralKalmanTracker(AlgorithmBackend[dict[str, Any]]):
    name = "MOTR+Neural-Kalman"

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        verified = inputs.get("verified_detections", [])
        prior_tracks = inputs.get("prior_tracks", [])
        if self.use_mock:
            return self._mock_track(verified, prior_tracks)
        return self._infer(verified, prior_tracks, inputs)

    def _mock_track(
        self, verified: list[dict[str, Any]], prior_tracks: list[dict[str, Any]]
    ) -> dict[str, Any]:
        tracks: list[dict[str, Any]] = []
        for i, det in enumerate(verified):
            track_id = f"T-{len(prior_tracks) + i + 1:04d}"
            bbox = det.get("bbox") or [0, 0, 0, 0]
            cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            tracks.append(
                {
                    "track_id": track_id,
                    "class_name": det.get("class_name", "unknown"),
                    "confidence": det.get("confidence", 0.0),
                    "state": "active",
                    "bbox": bbox,
                    "last_bbox": bbox,
                    "position_px": [cx, cy],
                    "geo": {
                        "lat": 30.5 + i * 0.001,
                        "lon": 114.3 + i * 0.001,
                        "alt_m": 120.0,
                    },
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
        )
