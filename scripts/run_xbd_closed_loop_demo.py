#!/usr/bin/env python3
import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from closed_loop_agent.closed_loop_core import _closed_loop_optimization
from scripts.extract_xbd_damage_features import extract_features


def _targets_from_feature_csv(path: Path) -> list:
    targets = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            target_id = f"{row.get('sample_id', 'xbd')}-{row.get('building_index', len(targets))}"
            targets.append(
                {
                    "target_id": target_id,
                    "target_class": "building",
                    "pre_area": float(row.get("pre_area") or 0.0),
                    "spectral_delta": float(row.get("spectral_delta") or 0.0),
                    "texture_delta": float(row.get("texture_delta") or 0.0),
                    "heat_signature": float(row.get("heat_signature") or 0.0),
                    "crater_density": float(row.get("crater_density") or 0.0),
                    "normalized_distance": float(row.get("normalized_distance") or 0.0),
                    "detection_confidence": float(row.get("detection_confidence") or 1.0),
                    "threat_score": float(row.get("threat_score") or 0.5),
                    "velocity_norm": 0.2,
                    "uncertainty": max(0.05, 1.0 - float(row.get("detection_confidence") or 1.0)),
                    "ammo_need": 0.3 + 0.4 * float(row.get("threat_score") or 0.5),
                }
            )
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the xBD feature extraction and closed-loop demo end to end.")
    parser.add_argument("--input-root", default="data/xbd/train", help="xBD sample root containing images/ and labels/.")
    parser.add_argument("--feature-csv", default="data/xbd/processed/xbd_damage_features.csv", help="Generated feature CSV.")
    parser.add_argument("--feature-report", default="data/xbd/processed/xbd_damage_features_report.json", help="Generated feature report JSON.")
    parser.add_argument("--result-json", default="data/xbd/processed/xbd_closed_loop_result.json", help="Closed-loop result JSON.")
    parser.add_argument("--cycles", type=int, default=3, help="Closed-loop cycles.")
    parser.add_argument(
        "--enforce-min-target-count",
        action="store_true",
        help="Pad/extend live targets to at least 50 to match the protocol target-count requirement.",
    )
    args = parser.parse_args()

    feature_csv = Path(args.feature_csv)
    feature_report = Path(args.feature_report)
    result_json = Path(args.result_json)

    report = extract_features(Path(args.input_root), feature_csv, feature_report)
    targets = _targets_from_feature_csv(feature_csv)
    result = _closed_loop_optimization(
        {
            "target_count": len(targets),
            "targets": targets,
            "cycles": args.cycles,
            "seed": 20260412,
            "enforce_min_target_count": bool(args.enforce_min_target_count),
            "dataset_paths": {"xbd_damage_csv": str(feature_csv)},
        }
    )
    result_json.parent.mkdir(parents=True, exist_ok=True)
    result_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "feature_report": report,
        "result_json": str(result_json),
        "damage_source": result["output_data"]["datasets"]["damage_assessment"],
        "mission_source": result["output_data"]["datasets"]["mission_evaluation"],
        "requirement_report": result["output_data"]["requirement_report"],
        "latency": result.get("latency"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
