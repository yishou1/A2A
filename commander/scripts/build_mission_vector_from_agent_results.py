#!/usr/bin/env python3
"""Aggregate upstream Agent results into closed-loop mission feature vectors."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from closed_loop_agent.agent_results_mapping import (  # noqa: E402
    MISSION_FEATURE_NAMES,
    mission_vector_from_results,
    mission_vector_to_csv_row,
    parse_results_json,
)

CSV_COLUMNS = [
    "replay_id",
    "player_id",
    "map_title",
    "game_version",
    "duration_sec",
    "mmr",
    "apm",
    "result",
    "race",
    *MISSION_FEATURE_NAMES,
    "task_completion",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build mission feature vector from Agent results JSON.")
    parser.add_argument(
        "--results-json",
        default="",
        help="Path to JSON payload containing results (or raw results object).",
    )
    parser.add_argument(
        "--task-completion",
        type=float,
        default=None,
        help="Optional task_completion label in [0,1].",
    )
    parser.add_argument(
        "--mission-id",
        default="mission-live-001",
        help="Mission/replay id used when writing CSV.",
    )
    parser.add_argument(
        "--map-title",
        default="live",
        help="Map title used when writing CSV.",
    )
    parser.add_argument(
        "--output-csv",
        default="",
        help="Optional CSV path to append one feature row.",
    )
    args = parser.parse_args()

    if args.results_json:
        text = Path(args.results_json).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    if not text.strip():
        raise SystemExit("No JSON input provided.")

    results = parse_results_json(text)
    vector = mission_vector_from_results(results)
    summary = {
        "feature_names": list(MISSION_FEATURE_NAMES),
        "mission_vector": vector,
        "results_keys": sorted(results.keys()),
    }

    task_completion = args.task_completion
    if task_completion is None:
        plan = results.get("plan_decision", {})
        plan_out = plan.get("output_data", {}) if isinstance(plan, dict) else {}
        kpi = plan_out.get("mission_kpi")
        task_completion = float(kpi) if kpi is not None else None

    if task_completion is not None:
        summary["task_completion"] = round(float(task_completion), 4)
        summary["csv_row"] = mission_vector_to_csv_row(
            args.mission_id,
            vector,
            float(task_completion),
            map_title=args.map_title,
        )

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.output_csv and task_completion is not None:
        csv_path = Path(args.output_csv)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not csv_path.exists() or csv_path.stat().st_size == 0
        with csv_path.open("a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
            if write_header:
                writer.writeheader()
            writer.writerow(summary["csv_row"])


if __name__ == "__main__":
    main()
