#!/usr/bin/env python3
"""Extract SC2LE task-level features from Blizzard replay packs (metadata-only).

Reads replay.gamemetadata.json inside each .SC2Replay via s2protocol/mpyq.
Does not require the StarCraft II client. Each player in a replay becomes one
CSV row for mission-completion model training/evaluation.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

import mpyq

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from closed_loop_agent.mission_feature_adapter import build_features_from_sc2le_proxy, verify_sc2le_proxy_no_result_leakage

OUTPUT_COLUMNS = [
    "replay_id",
    "player_id",
    "map_title",
    "game_version",
    "duration_sec",
    "mmr",
    "apm",
    "opponent_mmr",
    "result",
    "race",
    "damage_rate",
    "asset_readiness",
    "control_timeliness",
    "intel_confidence",
    "threat_pressure",
    "ammo_pressure",
    "comm_quality",
    "task_completion",
]


def _parse_replay_metadata(replay_path: Path) -> list[dict]:
    archive = mpyq.MPQArchive(io.BytesIO(replay_path.read_bytes()))
    extracted = archive.extract()
    meta = json.loads(extracted[b"replay.gamemetadata.json"].decode("utf-8"))
    players = meta.get("Players") or []
    if len(players) < 1:
        return []

    duration_sec = float(meta.get("Duration") or 0.0)
    rows: list[dict] = []
    mmrs = [float(player.get("MMR") or 3000.0) for player in players]

    for index, player in enumerate(players):
        opponent_mmr = mmrs[1 - index] if len(mmrs) > 1 else mmrs[0]
        bundle = build_features_from_sc2le_proxy(
            mmr=float(player.get("MMR") or 3000.0),
            apm=float(player.get("APM") or 120.0),
            duration_sec=duration_sec,
            opponent_mmr=opponent_mmr,
            result=str(player.get("Result") or ""),
        )
        rows.append(
            {
                "replay_id": replay_path.stem,
                "player_id": int(player.get("PlayerID") or index + 1),
                "map_title": str(meta.get("Title") or ""),
                "game_version": str(meta.get("GameVersion") or ""),
                "duration_sec": round(duration_sec, 3),
                "mmr": round(float(player.get("MMR") or 0.0), 3),
                "apm": round(float(player.get("APM") or 0.0), 3),
                "opponent_mmr": round(float(opponent_mmr), 3),
                "result": str(player.get("Result") or ""),
                "race": str(player.get("SelectedRace") or player.get("AssignedRace") or ""),
                **bundle["values"],
                "task_completion": bundle["label"]["task_completion"],
            }
        )
    return rows


def extract_features(
    replay_root: Path,
    output_csv: Path,
    *,
    limit: int = 0,
    report_json: Path | None = None,
) -> dict:
    replay_paths = sorted(replay_root.rglob("*.SC2Replay"))
    if limit > 0:
        replay_paths = replay_paths[:limit]

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    failed = 0

    for replay_path in replay_paths:
        try:
            rows.extend(_parse_replay_metadata(replay_path))
        except Exception:
            failed += 1

    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    leakage = verify_sc2le_proxy_no_result_leakage(
        mmr=3200.0,
        apm=180.0,
        duration_sec=900.0,
        opponent_mmr=3000.0,
    )
    report = {
        "replay_root": str(replay_root),
        "output_csv": str(output_csv),
        "replay_files_scanned": len(replay_paths),
        "feature_rows_written": len(rows),
        "failed_replays": failed,
        "feature_columns": OUTPUT_COLUMNS,
        "feature_version": "mission_features_v2",
        "label_leakage_check": {"passed": bool(leakage.get("passed"))},
        "notes": (
            "Features are derived from replay.gamemetadata.json (MMR, APM, Duration, opponent MMR). "
            "damage_rate is proxy_damage_rate and does NOT use Result/completion. "
            "task_completion uses Win=1.0 / Loss=0.0 as label only."
        ),
    }
    if report_json is not None:
        report_json.parent.mkdir(parents=True, exist_ok=True)
        report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract SC2LE task features from replay packs.")
    parser.add_argument(
        "--replay-root",
        default="data/sc2/3.16.1-Pack_1-fix/Replays",
        help="Directory containing .SC2Replay files.",
    )
    parser.add_argument(
        "--output-csv",
        default="data/sc2/processed/sc2le_task_features.csv",
        help="Output feature CSV path.",
    )
    parser.add_argument(
        "--report-json",
        default="data/sc2/processed/sc2le_task_features_report.json",
        help="Extraction summary JSON path.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process the first N replay files (0 = all).",
    )
    args = parser.parse_args()

    report = extract_features(
        Path(args.replay_root),
        Path(args.output_csv),
        limit=max(0, int(args.limit)),
        report_json=Path(args.report_json),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
