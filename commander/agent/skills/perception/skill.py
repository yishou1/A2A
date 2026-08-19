"""感知探测技能：RT-DETR+ODConv → Siamese Mask2Former → EDL → MOTR+Neural Kalman。

任务调度已拆至独立 `task_scheduling_agent`，本技能不再运行 MARL-PPO。
"""

from __future__ import annotations

from typing import Any

from agent.algorithm_library.factory import (
    create_damage_assessor,
    create_edl_verifier,
    create_motr_tracker,
    create_rt_detr_detector,
)
from agent.algorithm_library.planner_runtime import AlgorithmPlan
from agent.models.schemas import Detection, PerceptionOutput, SensorBatch


def _as_list(value: Any, *keys: str) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if isinstance(item, list):
                return item
    return []


def _algorithm_id(backend: Any) -> str:
    return str(getattr(backend, "algorithm_id", None) or getattr(backend, "name", "algorithm"))


def _planned_inputs(base: dict[str, Any], plan: AlgorithmPlan | None, algorithm_id: str) -> dict[str, Any]:
    if plan is None:
        return base
    enriched = dict(base)
    params = plan.params_for(algorithm_id)
    enriched["params"] = params
    for call in plan.algorithm_calls:
        if call.algorithm_id == algorithm_id:
            enriched["_algorithm_reason"] = call.reason
            break
    return enriched


def _run_and_record(backend: Any, inputs: dict[str, Any]) -> tuple[Any, dict[str, Any] | None]:
    if hasattr(backend, "last_invocation"):
        backend.last_invocation = None
    output = backend.run(inputs)
    invocation = getattr(backend, "last_invocation", None)
    if isinstance(invocation, dict):
        return output, dict(invocation)
    return output, None


class PerceptionSkill:
    def __init__(self, *, use_mock: bool = True, config: dict[str, Any] | None = None):
        cfg = config or {}
        self.detector = create_rt_detr_detector(use_mock=use_mock, config=cfg)
        self.damage = create_damage_assessor(use_mock=use_mock, config=cfg)
        self.edl = create_edl_verifier(use_mock=use_mock, config=cfg)
        self.tracker = create_motr_tracker(use_mock=use_mock, config=cfg)

    def execute(
        self,
        batch: SensorBatch,
        prior_tracks: list[dict[str, Any]] | None = None,
        *,
        plan: AlgorithmPlan | None = None,
    ) -> PerceptionOutput:
        frame_dicts = [f.model_dump(mode="json") for f in batch.frames]
        visual_frames = [f for f in frame_dicts if f.get("modality") in ("eo_ir", "sar")]

        def enabled(aid: str) -> bool:
            return plan is None or plan.is_enabled(aid)

        invocations: list[dict[str, Any]] = []
        detector_id = _algorithm_id(self.detector)
        detector_output, invocation = _run_and_record(
            self.detector,
            _planned_inputs({"frames": visual_frames}, plan, detector_id),
        )
        if invocation:
            invocations.append(invocation)
        raw_dets = _as_list(detector_output, "detections")
        for frame in frame_dicts:
            payload = frame.get("payload") if isinstance(frame.get("payload"), dict) else {}
            raw_dets.extend(
                item for item in _as_list(payload.get("detections"), "detections")
                if isinstance(item, dict)
            )
        trace = {self.detector.name: f"{len(raw_dets)} detections"}

        damage_reports: list[dict[str, Any]] = []
        if enabled("siamese_mask2former_damage"):
            ref = batch.context.get("reference_frame")
            damage_inputs: dict[str, Any] = {"frames": visual_frames}
            if isinstance(ref, dict):
                damage_inputs["reference_frame"] = ref
            damage_output, invocation = _run_and_record(
                self.damage,
                _planned_inputs(damage_inputs, plan, _algorithm_id(self.damage)),
            )
            if invocation:
                invocations.append(invocation)
            damage_reports = _as_list(
                damage_output,
                "damage_reports",
            )
            trace[self.damage.name] = f"{len(damage_reports)} damage masks"
        else:
            trace[self.damage.name] = "skipped"

        damage_by_sensor = {
            d["sensor_id"]: d for d in damage_reports if isinstance(d, dict) and "sensor_id" in d
        }
        for det in raw_dets:
            if not isinstance(det, dict):
                continue
            sid = det.get("sensor_id")
            if sid in damage_by_sensor:
                det["damage_score"] = damage_by_sensor[sid].get("damage_score")

        if enabled("edl_evidential_verifier"):
            edl_output, invocation = _run_and_record(
                self.edl,
                _planned_inputs({"detections": raw_dets}, plan, _algorithm_id(self.edl)),
            )
            if invocation:
                invocations.append(invocation)
            verified = _as_list(
                edl_output,
                "verified_detections",
                "detections",
            )
            if not verified:
                verified = list(raw_dets)
            trace[self.edl.name] = f"{len(verified)} verified"
        else:
            verified = list(raw_dets)
            trace[self.edl.name] = "skipped->passthrough"

        tracker_inputs: dict[str, Any] = {
            "verified_detections": verified,
            "prior_tracks": prior_tracks or [],
            "batch_context": batch.context,
        }
        if visual_frames:
            tracker_inputs["visual_frame"] = visual_frames[0]
        track_result, invocation = _run_and_record(
            self.tracker,
            _planned_inputs(tracker_inputs, plan, _algorithm_id(self.tracker)),
        )
        if invocation:
            invocations.append(invocation)
        if not isinstance(track_result, dict):
            track_result = {"tracks": []}
        tracks = track_result.get("tracks", []) or []
        trace[self.tracker.name] = f"{len(tracks)} tracks"
        # 调度由独立 task_scheduling_agent 负责，避免与 TIA 冲突
        trace["task_scheduling"] = "delegated_to_task_scheduling_agent"

        detections_by_track: dict[str, dict[str, Any]] = {}
        for det in verified:
            if not isinstance(det, dict):
                continue
            metadata = det.get("metadata") if isinstance(det.get("metadata"), dict) else {}
            track_id = det.get("track_id") or metadata.get("amos_track_id") or metadata.get("source_contact_id")
            if not track_id:
                continue
            existing = detections_by_track.get(str(track_id))
            existing_class = str((existing or {}).get("class_name") or "unknown").casefold()
            candidate_class = str(det.get("class_name") or "unknown").casefold()
            if existing is None or existing_class == "unknown" or candidate_class != "unknown":
                detections_by_track[str(track_id)] = det

        detections: list[Detection] = []
        for track in tracks:
            if not isinstance(track, dict):
                continue
            det = detections_by_track.get(str(track.get("track_id") or ""), {})
            detections.append(
                Detection(
                    track_id=track.get("track_id"),
                    sensor_id=det.get("sensor_id"),
                    class_name=track.get("class_name") or det.get("class_name", "unknown"),
                    confidence=float(track.get("confidence", det.get("confidence", 0)) or 0),
                    bbox=det.get("bbox"),
                    geo=track.get("geo"),
                    damage_score=det.get("damage_score"),
                    epistemic_uncertainty=det.get("epistemic_uncertainty"),
                )
            )

        return PerceptionOutput(
            detections=detections,
            tracks=tracks,
            verified_ids=[t["track_id"] for t in tracks if "track_id" in t],
            task_schedule=None,
            algorithm_trace=trace,
            algorithm_calls=[
                {
                    key: value
                    for key, value in invocation.items()
                    if key not in {"input", "output", "usage"}
                }
                for invocation in invocations
            ],
            algorithm_invocations=invocations,
        )
