"""感知探测技能：RT-DETR+ODConv → Siamese Mask2Former → EDL → MOTR+Neural Kalman。"""

from __future__ import annotations

from typing import Any

from agent.models.schemas import Detection, PerceptionOutput, SensorBatch
from agent.skills.base import subskill_config
from agent.skills.perception.edl import EDLEvidentialVerifier
from agent.skills.perception.motr_neural_kalman_tracker import MOTRNeuralKalmanTracker
from agent.skills.perception.rt_detr_odconv_detector import RTDETRODConvDetector
from agent.skills.perception.siamese_mask2former_damage import SiameseMask2FormerDamage


class PerceptionSkill:
    def __init__(self, *, use_mock: bool = True, config: dict[str, Any] | None = None):
        cfg = config or {}
        self.detector = RTDETRODConvDetector(
            use_mock=use_mock, config=subskill_config(cfg, "rt_detr_odconv")
        )
        self.damage = SiameseMask2FormerDamage(
            use_mock=use_mock, config=subskill_config(cfg, "siamese_mask2former")
        )
        self.edl = EDLEvidentialVerifier(use_mock=use_mock, config=subskill_config(cfg, "edl"))
        self.tracker = MOTRNeuralKalmanTracker(
            use_mock=use_mock, config=subskill_config(cfg, "motr_neural_kalman")
        )

    def execute(self, batch: SensorBatch, prior_tracks: list[dict[str, Any]] | None = None) -> PerceptionOutput:
        frame_dicts = [f.model_dump(mode="json") for f in batch.frames]
        visual_frames = [
            f for f in frame_dicts if f.get("modality") in ("eo_ir", "sar")
        ]

        raw_dets = self.detector.run({"frames": visual_frames})
        trace = {self.detector.name: f"{len(raw_dets)} detections"}

        ref = batch.context.get("reference_frame")
        damage_reports = self.damage.run(
            {"frames": visual_frames, "reference_frame": ref}
        )
        trace[self.damage.name] = f"{len(damage_reports)} damage masks"

        damage_by_sensor = {d["sensor_id"]: d for d in damage_reports if "sensor_id" in d}
        for det in raw_dets:
            sid = det.get("sensor_id")
            if sid in damage_by_sensor:
                det["damage_score"] = damage_by_sensor[sid].get("damage_score")

        verified = self.edl.run({"detections": raw_dets})
        trace[self.edl.name] = f"{len(verified)} verified"

        track_result = self.tracker.run(
            {
                "verified_detections": verified,
                "prior_tracks": prior_tracks or [],
                "visual_frame": visual_frames[0] if visual_frames else None,
                "batch_context": batch.context,
            }
        )
        trace[self.tracker.name] = f"{len(track_result.get('tracks', []))} tracks"

        detections: list[Detection] = []
        for det, track in zip(verified, track_result.get("tracks", [])):
            detections.append(
                Detection(
                    track_id=track.get("track_id"),
                    sensor_id=det.get("sensor_id"),
                    class_name=det.get("class_name", "unknown"),
                    confidence=float(det.get("confidence", 0)),
                    bbox=det.get("bbox"),
                    geo=track.get("geo"),
                    damage_score=det.get("damage_score"),
                    epistemic_uncertainty=det.get("epistemic_uncertainty"),
                )
            )

        return PerceptionOutput(
            detections=detections,
            tracks=track_result.get("tracks", []),
            verified_ids=[t["track_id"] for t in track_result.get("tracks", []) if "track_id" in t],
            algorithm_trace=trace,
        )
