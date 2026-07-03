#!/usr/bin/env python3
"""Train and persist SC2LE proxy mission model with replay_id grouped splits."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from closed_loop_agent.mission_model_service import train_sc2le_proxy_model, write_evaluation_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train SC2LE proxy mission model.")
    parser.add_argument(
        "--feature-csv",
        default="data/sc2/processed/sc2le_task_features.csv",
        help="Input feature CSV.",
    )
    parser.add_argument(
        "--model-path",
        default="models/sc2le_proxy_mission_model.pkl",
        help="Output model pickle path.",
    )
    parser.add_argument(
        "--metadata-path",
        default="models/sc2le_proxy_mission_model.metadata.json",
        help="Output metadata JSON path.",
    )
    parser.add_argument(
        "--report-json",
        default="data/sc2/processed/sc2le_mission_evaluation_report.json",
        help="Evaluation report JSON path.",
    )
    parser.add_argument("--seed", type=int, default=20260412, help="Random seed.")
    args = parser.parse_args()

    report = write_evaluation_report(
        Path(args.feature_csv),
        report_path=Path(args.report_json),
        seed=int(args.seed),
        model_path=Path(args.model_path),
        metadata_path=Path(args.metadata_path),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
