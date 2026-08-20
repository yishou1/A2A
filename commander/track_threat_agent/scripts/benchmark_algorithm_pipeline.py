"""Run a deterministic engineering benchmark for the online algorithm pipeline."""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from app.group_detector import GroupDetector
from app.models import Detection
from app.threat_ranker import ThreatRanker
from app.tracker import MultiTargetTracker
from app.utils import meters_to_lat_lon_delta, project_position, speed_heading_to_velocity


@dataclass(frozen=True)
class TruthTarget:
    truth_id: str
    object_type: str
    lat: float
    lon: float
    alt: float
    speed: float
    heading: float


def run_benchmark(frame_count: int = 90, seed: int = 42, max_p95_ms: float = 200.0) -> Dict[str, Any]:
    if frame_count < 2:
        raise ValueError("frame_count must be at least 2")
    rng = random.Random(seed)
    tracker = MultiTargetTracker()
    ranker = ThreatRanker()
    detector = GroupDetector()
    scene = {
        "protected_zone_lat": 31.2304,
        "protected_zone_lon": 121.4737,
        "protected_radius_m": 30_000.0,
    }
    targets = _truth_targets(scene["protected_zone_lat"], scene["protected_zone_lon"])
    expected_track_by_truth: Dict[str, str] = {}
    association_observations = 0
    correct_associations = 0
    id_switches = 0
    maximum_false_tracks = 0
    maximum_history_points = 0
    explained_rankings = 0
    ranking_rows = 0
    group_id_churn = 0
    previous_group_ids: Dict[str, str] = {}
    latencies_ms: List[float] = []

    for frame_index in range(frame_count):
        detections = _frame_detections(targets, frame_index, rng)
        started = time.perf_counter()
        tracks = tracker.update(detections, algorithm_level="medium")
        threats = ranker.rank(tracks, scene)
        groups = detector.detect(tracks, threats, scene)
        latencies_ms.append((time.perf_counter() - started) * 1000.0)

        current_by_detection = {
            str(track.metadata.get("last_detection_id")): track.track_id
            for track in tracks
            if track.metadata.get("last_detection_id")
        }
        for detection in detections:
            observed_track_id = current_by_detection.get(detection.detection_id)
            if observed_track_id is None:
                continue
            truth_id = str(detection.metadata["truth_id"])
            if frame_index == 0:
                expected_track_by_truth[truth_id] = observed_track_id
                continue
            association_observations += 1
            if observed_track_id == expected_track_by_truth.get(truth_id):
                correct_associations += 1
            else:
                id_switches += 1

        maximum_false_tracks = max(maximum_false_tracks, max(0, len(tracks) - len(targets)))
        maximum_history_points = max(
            maximum_history_points,
            max((len(track.history_path) for track in tracks), default=0),
        )
        ranking_rows += len(threats)
        explained_rankings += sum(
            bool(threat.evidence)
            and "prediction_risk_context" in threat.metadata
            and "dbn" in threat.metadata
            for threat in threats
        )

        group_id_churn += _count_group_churn(groups, expected_track_by_truth, previous_group_ids)

    association_accuracy = correct_associations / max(association_observations, 1)
    explanation_coverage = explained_rankings / max(ranking_rows, 1)
    p50_ms = float(np.percentile(latencies_ms, 50))
    p95_ms = float(np.percentile(latencies_ms, 95))
    gates = {
        "association_accuracy_at_least_99_percent": association_accuracy >= 0.99,
        "zero_id_switches": id_switches == 0,
        "zero_false_tracks": maximum_false_tracks == 0,
        "group_id_churn_at_most_one": group_id_churn <= 1,
        "ranking_explanation_coverage_100_percent": explanation_coverage == 1.0,
        "history_path_at_most_50": maximum_history_points <= 50,
        "pipeline_cpu_p95_within_budget": p95_ms <= max_p95_ms,
    }
    return {
        "benchmark": "track_threat_algorithm_pipeline/v1",
        "scope": "deterministic engineering regression; not a substitute for customer field data",
        "seed": seed,
        "frame_count": frame_count,
        "target_count": len(targets),
        "metrics": {
            "association_observations": association_observations,
            "association_accuracy": round(association_accuracy, 6),
            "id_switches": id_switches,
            "maximum_false_tracks": maximum_false_tracks,
            "group_id_churn": group_id_churn,
            "ranking_explanation_coverage": round(explanation_coverage, 6),
            "maximum_history_points": maximum_history_points,
            "pipeline_p50_ms": round(p50_ms, 3),
            "pipeline_p95_ms": round(p95_ms, 3),
            "pipeline_max_p95_ms": max_p95_ms,
        },
        "gates": gates,
        "passed": all(gates.values()),
    }


def _truth_targets(reference_lat: float, reference_lon: float) -> List[TruthTarget]:
    specifications = [
        ("air-1", "aircraft", -18_000.0, -20_000.0, 5_500.0, 180.0, 135.0),
        ("air-2", "aircraft", -17_300.0, -20_400.0, 5_600.0, 180.0, 135.0),
        ("air-3", "aircraft", -18_500.0, -19_300.0, 5_450.0, 180.0, 135.0),
        ("ship-1", "ship", 12_000.0, 28_000.0, 0.0, 11.0, 255.0),
        ("ship-2", "ship", 12_800.0, 28_600.0, 0.0, 11.0, 255.0),
        ("cross-a", "uav", 5_000.0, -12_000.0, 1_800.0, 70.0, 90.0),
        ("cross-b", "uav", 5_000.0, 12_000.0, 1_850.0, 70.0, 270.0),
    ]
    targets = []
    for truth_id, object_type, north_m, east_m, alt, speed, heading in specifications:
        delta_lat, delta_lon = meters_to_lat_lon_delta(north_m, east_m, reference_lat)
        targets.append(
            TruthTarget(
                truth_id=truth_id,
                object_type=object_type,
                lat=reference_lat + delta_lat,
                lon=reference_lon + delta_lon,
                alt=alt,
                speed=speed,
                heading=heading,
            )
        )
    return targets


def _frame_detections(
    targets: List[TruthTarget],
    frame_index: int,
    rng: random.Random,
) -> List[Detection]:
    timestamp = float(frame_index * 10)
    detections = []
    for target_index, target in enumerate(targets):
        if frame_index > 0 and frame_index % 17 == target_index + 1:
            continue
        vx, vy = speed_heading_to_velocity(target.speed, target.heading)
        lat, lon = project_position(target.lat, target.lon, vx, vy, timestamp)
        noise_east_m = rng.gauss(0.0, 8.0)
        noise_north_m = rng.gauss(0.0, 8.0)
        noise_lat, noise_lon = meters_to_lat_lon_delta(noise_north_m, noise_east_m, lat)
        detections.append(
            Detection(
                detection_id=f"{target.truth_id}-frame-{frame_index:04d}",
                object_type=target.object_type,
                timestamp=timestamp,
                lat=lat + noise_lat,
                lon=lon + noise_lon,
                alt=target.alt,
                speed=target.speed,
                heading=target.heading,
                confidence=0.95,
                source_agent="algorithm-benchmark",
                metadata={"truth_id": target.truth_id},
            )
        )
    rng.shuffle(detections)
    return detections


def _count_group_churn(
    groups: List[Any],
    expected_track_by_truth: Dict[str, str],
    previous_group_ids: Dict[str, str],
) -> int:
    churn = 0
    expected_members = {
        "air_formation": {
            expected_track_by_truth.get("air-1"),
            expected_track_by_truth.get("air-2"),
            expected_track_by_truth.get("air-3"),
        },
        "surface_group": {
            expected_track_by_truth.get("ship-1"),
            expected_track_by_truth.get("ship-2"),
        },
    }
    for group_type, members in expected_members.items():
        valid_members = {member for member in members if member}
        if len(valid_members) < 2:
            continue
        candidates = [
            group
            for group in groups
            if group.group_type == group_type and len(set(group.member_track_ids) & valid_members) >= 2
        ]
        if not candidates:
            continue
        group = max(candidates, key=lambda item: len(set(item.member_track_ids) & valid_members))
        previous = previous_group_ids.get(group_type)
        if previous is not None and previous != group.group_id:
            churn += 1
        previous_group_ids[group_type] = group.group_id
    return churn


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Track Threat Agent algorithms.")
    parser.add_argument("--frames", type=int, default=90)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-p95-ms", type=float, default=200.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_benchmark(args.frames, args.seed, args.max_p95_ms)
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
