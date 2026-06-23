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


def _targets_from_feature_csv(path: Path, limit: int = 0) -> list:
    targets = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            target_id = f"{row.get('sample_id', 'xbd')}-{row.get('building_index', len(targets))}"
            targets.append(
                {
                    "target_id": target_id,
                    "sample_id": row.get("sample_id") or target_id.rsplit("-", 1)[0],
                    "building_index": row.get("building_index", len(targets)),
                    "target_class": "building",
                    "pre_area": float(row.get("pre_area") or 0.0),
                    "spectral_delta": float(row.get("spectral_delta") or 0.0),
                    "texture_delta": float(row.get("texture_delta") or 0.0),
                    "heat_signature": float(row.get("heat_signature") or 0.0),
                    "crater_density": float(row.get("crater_density") or 0.0),
                    "std_spectral": float(row.get("std_spectral") or 0.0),
                    "max_spectral": float(row.get("max_spectral") or 0.0),
                    "high_change_ratio": float(row.get("high_change_ratio") or 0.0),
                    "severe_damage_ratio": float(row.get("severe_damage_ratio") or 0.0),
                    "collapse_ratio": float(row.get("collapse_ratio") or 0.0),
                    "post_brightness": float(row.get("post_brightness") or 0.0),
                    "brightness_drop": float(row.get("brightness_drop") or 0.0),
                    "normalized_distance": float(row.get("normalized_distance") or 0.0),
                    "detection_confidence": float(row.get("detection_confidence") or 1.0),
                    "threat_score": float(row.get("threat_score") or 0.5),
                    "velocity_norm": 0.2,
                    "uncertainty": max(0.05, 1.0 - float(row.get("detection_confidence") or 1.0)),
                    "ammo_need": 0.3 + 0.4 * float(row.get("threat_score") or 0.5),
                }
            )
            if limit and len(targets) >= limit:
                break
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and test closed-loop models on full xBD feature CSV.")
    parser.add_argument(
        "--feature-csv",
        default="data/xbd/processed/xbd_damage_features_train.csv",
        help="Full training feature table.",
    )
    parser.add_argument(
        "--cnn-npz",
        default="data/xbd/processed/xbd_cnn_embeddings_train.npz",
        help="Precomputed ResNet18 ROI embeddings.",
    )
    parser.add_argument(
        "--result-json",
        default="data/xbd/processed/xbd_closed_loop_result_cnn_lr.json",
        help="Output result JSON.",
    )
    parser.add_argument("--cycles", type=int, default=3, help="Closed-loop cycles.")
    parser.add_argument(
        "--live-target-limit",
        type=int,
        default=200,
        help="How many targets to use for live closed-loop simulation (0 = all).",
    )
    args = parser.parse_args()

    feature_csv = Path(args.feature_csv)
    cnn_npz = Path(args.cnn_npz)
    result_json = Path(args.result_json)
    live_limit = max(0, int(args.live_target_limit))

    print(f"loading feature table: {feature_csv}", flush=True)
    live_targets = _targets_from_feature_csv(feature_csv, live_limit if live_limit else 0)
    print(f"feature rows available for training: full csv", flush=True)
    print(f"live closed-loop targets: {len(live_targets)}", flush=True)

    result = _closed_loop_optimization(
        {
            "target_count": len(live_targets),
            "targets": live_targets,
            "cycles": args.cycles,
            "seed": 20260412,
            "dataset_paths": {
                "xbd_damage_csv": str(feature_csv),
                "xbd_cnn_npz": str(cnn_npz),
            },
        }
    )

    result_json.parent.mkdir(parents=True, exist_ok=True)
    result_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    output = result["output_data"]
    summary = {
        "result_json": str(result_json),
        "damage_source": output["datasets"]["damage_assessment"],
        "mission_source": output["datasets"]["mission_evaluation"],
        "performance_report": output.get("performance_report", {}),
        "requirement_report": output.get("requirement_report", {}),
        "live_target_count": len(live_targets),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
