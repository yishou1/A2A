#!/usr/bin/env python3
"""Extract SC2LE features, train frozen proxy model, and run closed-loop evaluation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from closed_loop_agent.closed_loop_core import _closed_loop_optimization
from closed_loop_agent.mission_model_service import write_evaluation_report
from scripts.extract_sc2le_task_features import extract_features


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract SC2LE features and run closed-loop evaluation.")
    parser.add_argument(
        "--replay-root",
        default="data/sc2/3.16.1-Pack_1-fix/Replays",
        help="Directory containing .SC2Replay files.",
    )
    parser.add_argument(
        "--feature-csv",
        default="data/sc2/processed/sc2le_task_features.csv",
        help="SC2LE feature CSV output path.",
    )
    parser.add_argument(
        "--feature-report",
        default="data/sc2/processed/sc2le_task_features_report.json",
        help="Feature extraction report JSON.",
    )
    parser.add_argument(
        "--model-path",
        default="models/sc2le_proxy_mission_model.pkl",
        help="Frozen mission model pickle path.",
    )
    parser.add_argument(
        "--metadata-path",
        default="models/sc2le_proxy_mission_model.metadata.json",
        help="Mission model metadata JSON path.",
    )
    parser.add_argument(
        "--evaluation-report",
        default="data/sc2/processed/sc2le_mission_evaluation_report.json",
        help="Mission evaluation report JSON path.",
    )
    parser.add_argument(
        "--result-json",
        default="data/sc2/processed/sc2le_closed_loop_result.json",
        help="Closed-loop evaluation result JSON.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process the first N replay files during extraction (0 = all).",
    )
    parser.add_argument(
        "--skip-extract",
        action="store_true",
        help="Reuse an existing feature CSV instead of re-extracting.",
    )
    parser.add_argument(
        "--skip-train",
        action="store_true",
        help="Reuse an existing frozen model instead of retraining.",
    )
    parser.add_argument("--cycles", type=int, default=3, help="Closed-loop cycles.")
    parser.add_argument("--seed", type=int, default=20260412, help="Random seed.")
    args = parser.parse_args()

    feature_csv = Path(args.feature_csv)
    model_path = Path(args.model_path)
    metadata_path = Path(args.metadata_path)
    evaluation_report_path = Path(args.evaluation_report)

    if not args.skip_extract:
        report = extract_features(
            Path(args.replay_root),
            feature_csv,
            limit=max(0, int(args.limit)),
            report_json=Path(args.feature_report),
        )
        print(json.dumps({"feature_extraction": report}, ensure_ascii=False, indent=2))

    if not args.skip_train:
        evaluation_report = write_evaluation_report(
            feature_csv,
            report_path=evaluation_report_path,
            seed=int(args.seed),
            model_path=model_path,
            metadata_path=metadata_path,
        )
        print(json.dumps({"mission_evaluation": evaluation_report}, ensure_ascii=False, indent=2))

    result = _closed_loop_optimization(
        {
            "target_count": 50,
            "cycles": args.cycles,
            "seed": args.seed,
            "enforce_min_target_count": True,
            "feature_mode": "hybrid",
            "dataset_paths": {
                "sc2le_task_csv": str(feature_csv),
                "mission_model_path": str(model_path),
                "mission_model_metadata_path": str(metadata_path),
            },
        }
    )

    result_json = Path(args.result_json)
    result_json.parent.mkdir(parents=True, exist_ok=True)
    result_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    output = result["output_data"]
    summary = {
        "result_json": str(result_json),
        "evaluation_report": str(evaluation_report_path),
        "mission_source": output["datasets"]["mission_evaluation"],
        "requirement_report": output.get("requirement_report", {}),
        "performance_report": output.get("performance_report", {}),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
