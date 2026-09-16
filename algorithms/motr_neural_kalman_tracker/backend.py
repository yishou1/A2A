"""MOTR + Neural Kalman Filter：端到端多目标跟踪与定位。

mock 路径不依赖 agent；真推理延迟导入 agent.inference.tracking（含 geo）。
"""

from __future__ import annotations

from typing import Any

from algorithms.base import AlgorithmBackend


class MOTRNeuralKalmanTracker(AlgorithmBackend[dict[str, Any]]):
    name = "MOTR+Neural-Kalman"
    algorithm_id = "motr_neural_kalman_tracker"
    config_key = "motr_neural_kalman"

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
        prior_geo_by_id = {
            str(pt.get("track_id", "")): pt.get("geo")
            for pt in prior_tracks
            if pt.get("track_id") and pt.get("geo")
        }
        batch_context = inputs.get("batch_context") if isinstance(inputs.get("batch_context"), dict) else {}

        tracks: list[dict[str, Any]] = []
        for i, det in enumerate(verified):
            track_id = f"T-{len(prior_tracks) + i + 1:04d}"
            bbox = det.get("bbox") or [0, 0, 0, 0]
            cx = (float(bbox[0]) + float(bbox[2])) / 2
            cy = (float(bbox[1]) + float(bbox[3])) / 2
            prior_geo = prior_geo_by_id.get(track_id)
            # 独立 mock geo：优先沿用 prior；否则用 batch_context 原点占位
            if isinstance(prior_geo, dict):
                geo = dict(prior_geo)
            else:
                geo = {
                    "lat": float(batch_context.get("origin_lat", 0.0)),
                    "lon": float(batch_context.get("origin_lon", 0.0)),
                    "alt_m": float(batch_context.get("origin_alt_m", 0.0)),
                    "geo_method": "mock_placeholder",
                    "class_name": str(det.get("class_name", "unknown")),
                }
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
