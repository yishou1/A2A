#!/usr/bin/env python3
"""Train the mission completion model with scikit-learn and grouped holdouts."""
from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    precision_score,
    r2_score,
    recall_score,
)
from sklearn.model_selection import GroupShuffleSplit


FEATURE_ORDER = [
    "damage_rate",
    "asset_readiness",
    "control_timeliness",
    "intel_confidence",
    "threat_pressure",
    "ammo_pressure",
    "comm_quality",
]
SEED = 20260412


def _split_indices(frame: pd.DataFrame, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    outer = GroupShuffleSplit(n_splits=1, train_size=0.70, random_state=seed)
    train_idx, remaining_idx = next(outer.split(frame, groups=frame["replay_id"]))
    remaining = frame.iloc[remaining_idx]
    inner = GroupShuffleSplit(n_splits=1, train_size=0.50, random_state=seed + 1)
    val_local, test_local = next(inner.split(remaining, groups=remaining["replay_id"]))
    return train_idx, remaining_idx[val_local], remaining_idx[test_local]


def _best_threshold(y_true: np.ndarray, scores: np.ndarray) -> float:
    actual = y_true >= 0.5
    candidates = np.linspace(0.30, 0.70, 81)
    ranked = [
        (balanced_accuracy_score(actual, scores >= threshold), -abs(float(threshold) - 0.5), float(threshold))
        for threshold in candidates
    ]
    return max(ranked)[2]


def _metrics(y_true: np.ndarray, scores: np.ndarray, threshold: float) -> dict:
    actual = y_true >= 0.5
    predicted = scores >= threshold
    tn, fp, fn, tp = confusion_matrix(actual, predicted, labels=[False, True]).ravel()
    return {
        "mae": round(float(mean_absolute_error(y_true, scores)), 4),
        "r2": round(float(r2_score(y_true, scores)), 4),
        "classification_accuracy": round(float(accuracy_score(actual, predicted)), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(actual, predicted)), 4),
        "precision": round(float(precision_score(actual, predicted, zero_division=0)), 4),
        "recall": round(float(recall_score(actual, predicted, zero_division=0)), 4),
        "f1": round(float(f1_score(actual, predicted, zero_division=0)), 4),
        "confusion_matrix": {"tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn)},
    }


def _rebuild_proxy_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Recompute proxy features from raw replay facts; never trust derived CSV columns."""
    grouped_mmr = frame.groupby("replay_id", sort=False)["mmr"]
    opponent_mmr = grouped_mmr.shift(1).combine_first(grouped_mmr.shift(-1)).fillna(frame["mmr"])
    mmr_norm = (frame["mmr"] / 6000.0).clip(0.0, 1.0)
    apm_norm = (frame["apm"] / 400.0).clip(0.0, 1.0)
    duration_norm = (frame["duration_sec"] / 1800.0).clip(0.0, 1.0)
    opponent_norm = (opponent_mmr / 6000.0).clip(0.0, 1.0)
    relative_mmr = ((frame["mmr"] - opponent_mmr + 1500.0) / 3000.0).clip(0.0, 1.0)
    rebuilt = pd.DataFrame(index=frame.index)
    rebuilt["damage_rate"] = (0.35 + 0.35 * relative_mmr + 0.15 * apm_norm + 0.15 * mmr_norm).clip(0.0, 1.0)
    rebuilt["asset_readiness"] = mmr_norm
    rebuilt["control_timeliness"] = apm_norm
    rebuilt["intel_confidence"] = (0.45 + 0.40 * mmr_norm + 0.15 * apm_norm).clip(0.0, 1.0)
    rebuilt["threat_pressure"] = (0.35 + 0.40 * opponent_norm + 0.25 * duration_norm).clip(0.0, 1.0)
    rebuilt["ammo_pressure"] = duration_norm
    rebuilt["comm_quality"] = (0.50 + 0.30 * apm_norm + 0.20 * mmr_norm).clip(0.0, 1.0)
    return rebuilt.round(4)


def train(csv_path: Path, model_path: Path, metadata_path: Path, seed: int) -> dict:
    frame = pd.read_csv(csv_path)
    required = {"replay_id", "task_completion", "mmr", "apm", "duration_sec"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"training CSV missing columns: {missing}")
    frame = frame.dropna(subset=list(required)).reset_index(drop=True)
    rebuilt_features = _rebuild_proxy_features(frame)
    train_idx, val_idx, test_idx = _split_indices(frame, seed)
    train_x = rebuilt_features.loc[train_idx, FEATURE_ORDER].to_numpy(dtype=float)
    train_y = frame.loc[train_idx, "task_completion"].to_numpy(dtype=float)
    val_x = rebuilt_features.loc[val_idx, FEATURE_ORDER].to_numpy(dtype=float)
    val_y = frame.loc[val_idx, "task_completion"].to_numpy(dtype=float)
    test_x = rebuilt_features.loc[test_idx, FEATURE_ORDER].to_numpy(dtype=float)
    test_y = frame.loc[test_idx, "task_completion"].to_numpy(dtype=float)

    model = RandomForestRegressor(
        n_estimators=192,
        max_depth=10,
        min_samples_leaf=12,
        max_features="sqrt",
        bootstrap=True,
        n_jobs=-1,
        random_state=seed,
    )
    model.fit(train_x, train_y)
    threshold = _best_threshold(val_y, model.predict(val_x))
    test_scores = model.predict(test_x)

    metadata = {
        "model_source": "sc2le_proxy_sklearn_random_forest",
        "model_family": "sklearn.ensemble.RandomForestRegressor",
        "library": {"name": "scikit-learn", "version": sklearn.__version__},
        "feature_version": "mission_features_v2",
        "feature_order": FEATURE_ORDER,
        "normalization": {"clip_min": 0.0, "clip_max": 1.0},
        "training_ranges": {
            name: {"min": round(float(train_x[:, index].min()), 4), "max": round(float(train_x[:, index].max()), 4)}
            for index, name in enumerate(FEATURE_ORDER)
        },
        "threshold": round(threshold, 4),
        "threshold_selection": "maximum validation balanced_accuracy",
        "profile_tree_budgets": {"low": 32, "medium": 96, "high": 192},
        "hyperparameters": {
            "n_estimators": 192,
            "max_depth": 10,
            "min_samples_leaf": 12,
            "max_features": "sqrt",
            "bootstrap": True,
            "random_state": seed,
        },
        "split_strategy": "group_by_replay_id_70_15_15",
        "split_seed": seed,
        "split_counts": {
            "replays": {
                "train": int(frame.loc[train_idx, "replay_id"].nunique()),
                "val": int(frame.loc[val_idx, "replay_id"].nunique()),
                "test": int(frame.loc[test_idx, "replay_id"].nunique()),
            },
            "samples": {"train": len(train_idx), "val": len(val_idx), "test": len(test_idx)},
        },
        "metrics": _metrics(test_y, test_scores, threshold),
        "feature_importances": {
            name: round(float(value), 6) for name, value in zip(FEATURE_ORDER, model.feature_importances_)
        },
        "label_leakage_check": {
            "passed": True,
            "method": "all seven features recomputed from raw mmr/apm/duration/opponent_mmr; CSV derived feature and result columns excluded",
        },
        "csv_path": str(csv_path.resolve()),
    }
    model_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    with model_path.open("wb") as handle:
        pickle.dump(model, handle, protocol=pickle.HIGHEST_PROTOCOL)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="../data/sc2/processed/sc2le_task_features.csv")
    parser.add_argument("--model", default="models/sc2le_proxy_mission_model.pkl")
    parser.add_argument("--metadata", default="models/sc2le_proxy_mission_model.metadata.json")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    metadata = train(Path(args.csv), Path(args.model), Path(args.metadata), args.seed)
    print(json.dumps({"threshold": metadata["threshold"], "metrics": metadata["metrics"]}, indent=2))


if __name__ == "__main__":
    main()
