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

OUTPUT_COLUMNS = [
    "replay_id",
    "player_id",
    "map_title",
    "game_version",
    "duration_sec",
    "mmr",
    "apm",
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


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _result_to_completion(result: str) -> float:
    normalized = str(result or "").strip().lower()
    if normalized in {"win", "victory"}:
        return 1.0
    if normalized in {"loss", "defeat"}:
        return 0.0
    if normalized in {"tie", "draw", "undecided"}:
        return 0.5
    return 0.5


def _features_from_player(player: dict, *, duration_sec: float, opponent_mmr: float) -> dict:
    mmr = float(player.get("MMR") or 3000.0)
    apm = float(player.get("APM") or 120.0)
    completion = _result_to_completion(player.get("Result"))
    mmr_norm = _clamp(mmr / 6000.0)
    apm_norm = _clamp(apm / 400.0)
    duration_norm = _clamp(duration_sec / 1800.0)
    opponent_norm = _clamp(opponent_mmr / 6000.0)
    relative_mmr = _clamp((mmr - opponent_mmr + 1500.0) / 3000.0)

    return {
        "damage_rate": round(_clamp(0.25 + 0.55 * completion + 0.20 * relative_mmr), 4),
        "asset_readiness": round(mmr_norm, 4),
        "control_timeliness": round(apm_norm, 4),
        "intel_confidence": round(_clamp(0.45 + 0.40 * mmr_norm + 0.15 * apm_norm), 4),
        "threat_pressure": round(_clamp(0.35 + 0.40 * opponent_norm + 0.25 * duration_norm), 4),
        "ammo_pressure": round(duration_norm, 4),
        "comm_quality": round(_clamp(0.50 + 0.30 * apm_norm + 0.20 * mmr_norm), 4),
        "task_completion": round(completion, 4),
    }


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
        feature_values = _features_from_player(
            player,
            duration_sec=duration_sec,
            opponent_mmr=opponent_mmr,
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
                "result": str(player.get("Result") or ""),
                "race": str(player.get("SelectedRace") or player.get("AssignedRace") or ""),
                **feature_values,
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

    report = {
        "replay_root": str(replay_root),
        "output_csv": str(output_csv),
        "replay_files_scanned": len(replay_paths),
        "feature_rows_written": len(rows),
        "failed_replays": failed,
        "feature_columns": OUTPUT_COLUMNS,
        "notes": (
            "Features are derived from replay.gamemetadata.json (MMR, APM, Result, Duration). "
            "task_completion uses Win=1.0 / Loss=0.0. Other columns are normalized proxy features."
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
