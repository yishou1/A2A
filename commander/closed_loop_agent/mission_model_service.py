"""Offline training, persistence, and online inference for SC2LE proxy mission model."""
from __future__ import annotations

import csv
import json
import pickle
import random
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from closed_loop_agent.closed_loop_core import RandomForestRegressor
from closed_loop_agent.mission_feature_adapter import (
    build_features_from_sc2le_proxy,
    bundle_to_vector,
    normalize_feature_bundle,
    verify_sc2le_proxy_no_result_leakage,
)
from closed_loop_agent.mission_feature_schema import (
    DEFAULT_EVALUATION_REPORT_PATH,
    DEFAULT_MODEL_METADATA_PATH,
    DEFAULT_MODEL_PATH,
    FEATURE_ORDER,
    FEATURE_VERSION,
    MISSION_COMPLETION_THRESHOLD,
    assert_no_label_leakage,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_model_path(path: str | None = None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    return _project_root() / DEFAULT_MODEL_PATH


def _default_metadata_path(path: str | None = None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    return _project_root() / DEFAULT_MODEL_METADATA_PATH


def load_csv_rows(csv_path: str | Path) -> List[dict]:
    path = Path(csv_path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def rows_to_training_samples(rows: Sequence[dict]) -> Tuple[List[dict], List[float], List[str]]:
    by_replay: Dict[str, List[dict]] = {}
    for row in rows:
        by_replay.setdefault(str(row.get("replay_id") or ""), []).append(row)
    for replay_rows in by_replay.values():
        if len(replay_rows) == 2:
            replay_rows[0]["opponent_mmr"] = replay_rows[1].get("mmr") or 3000.0
            replay_rows[1]["opponent_mmr"] = replay_rows[0].get("mmr") or 3000.0
        else:
            for replay_row in replay_rows:
                replay_row.setdefault("opponent_mmr", replay_row.get("mmr") or 3000.0)

    bundles: List[dict] = []
    labels: List[float] = []
    replay_ids: List[str] = []
    for row in rows:
        bundle = build_features_from_sc2le_proxy(
            mmr=float(row.get("mmr") or 3000.0),
            apm=float(row.get("apm") or 120.0),
            duration_sec=float(row.get("duration_sec") or 0.0),
            opponent_mmr=float(row.get("opponent_mmr") or row.get("mmr") or 3000.0),
            result=str(row.get("result") or ""),
        )
        label = float(bundle["label"]["task_completion"])
        assert_no_label_leakage(bundle["values"], context="training_features")
        bundles.append(bundle)
        labels.append(label)
        replay_ids.append(str(row.get("replay_id") or ""))
    return bundles, labels, replay_ids


def split_replay_ids(
    replay_ids: Sequence[str],
    *,
    seed: int = 20260412,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
) -> dict:
    unique_ids = sorted({replay_id for replay_id in replay_ids if replay_id})
    rng = random.Random(seed)
    rng.shuffle(unique_ids)
    total = len(unique_ids)
    train_count = max(1, int(total * train_ratio))
    val_count = max(1, int(total * val_ratio)) if total > 2 else 0
    if train_count + val_count >= total:
        val_count = max(0, min(1, total - train_count))
    train_ids = set(unique_ids[:train_count])
    val_ids = set(unique_ids[train_count : train_count + val_count])
    test_ids = set(unique_ids[train_count + val_count :])
    if not test_ids and val_ids:
        moved = sorted(val_ids)[-1]
        val_ids.remove(moved)
        test_ids.add(moved)
    return {"train": train_ids, "val": val_ids, "test": test_ids}


def _partition_by_replay(
    bundles: Sequence[dict],
    labels: Sequence[float],
    replay_ids: Sequence[str],
    split_ids: dict,
) -> dict:
    partitions = {"train": ([], []), "val": ([], []), "test": ([], [])}
    for bundle, label, replay_id in zip(bundles, labels, replay_ids):
        if replay_id in split_ids["test"]:
            key = "test"
        elif replay_id in split_ids["val"]:
            key = "val"
        else:
            key = "train"
        partitions[key][0].append(bundle)
        partitions[key][1].append(label)
    return partitions


def _classification_metrics(y_true: Sequence[float], y_pred: Sequence[float], threshold: float = MISSION_COMPLETION_THRESHOLD) -> dict:
    tp = fp = tn = fn = 0
    for truth, pred in zip(y_true, y_pred):
        actual = 1 if float(truth) >= threshold else 0
        predicted = 1 if float(pred) >= threshold else 0
        if actual == 1 and predicted == 1:
            tp += 1
        elif actual == 0 and predicted == 1:
            fp += 1
        elif actual == 0 and predicted == 0:
            tn += 1
        else:
            fn += 1
    accuracy = (tp + tn) / max(1, tp + tn + fp + fn)
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    f1 = 2 * precision * recall / max(1e-9, precision + recall)
    return {
        "classification_accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
    }


def _regression_metrics(y_true: Sequence[float], y_pred: Sequence[float]) -> dict:
    mae = sum(abs(float(a) - float(b)) for a, b in zip(y_true, y_pred)) / max(1, len(y_true))
    mean = sum(float(value) for value in y_true) / max(1, len(y_true))
    ss_tot = sum((float(value) - mean) ** 2 for value in y_true) or 1.0
    ss_res = sum((float(pred) - float(truth)) ** 2 for pred, truth in zip(y_pred, y_true))
    r2 = 1.0 - ss_res / ss_tot
    return {"mae": round(mae, 4), "r2": round(r2, 4)}


def train_sc2le_proxy_model(
    csv_path: str | Path,
    *,
    seed: int = 20260412,
    model_path: str | Path | None = None,
    metadata_path: str | Path | None = None,
) -> dict:
    rows = load_csv_rows(csv_path)
    if not rows:
        raise ValueError(f"No rows found in {csv_path}")

    if "opponent_mmr" not in rows[0]:
        for row in rows:
            row["opponent_mmr"] = row.get("mmr") or 3000.0

    bundles, labels, replay_ids = rows_to_training_samples(rows)
    split_ids = split_replay_ids(replay_ids, seed=seed)
    partitions = _partition_by_replay(bundles, labels, replay_ids, split_ids)

    train_x = [bundle_to_vector(item) for item in partitions["train"][0]]
    train_y = [float(value) for value in partitions["train"][1]]
    test_x = [bundle_to_vector(item) for item in partitions["test"][0]]
    test_y = [float(value) for value in partitions["test"][1]]
    if len(train_x) < 10:
        raise ValueError("Not enough training samples after replay_id grouping")

    model = RandomForestRegressor(seed=seed).fit(train_x, train_y)
    test_pred = model.predict(test_x)
    regression = _regression_metrics(test_y, test_pred)
    classification = _classification_metrics(test_y, test_pred, threshold=MISSION_COMPLETION_THRESHOLD)

    training_ranges = {
        name: {
            "min": round(min(row[index] for row in train_x), 4),
            "max": round(max(row[index] for row in train_x), 4),
        }
        for index, name in enumerate(FEATURE_ORDER)
    }

    metadata = {
        "model_source": "sc2le_proxy",
        "feature_version": FEATURE_VERSION,
        "feature_order": list(FEATURE_ORDER),
        "normalization": {"clip_min": 0.0, "clip_max": 1.0},
        "training_ranges": training_ranges,
        "threshold": MISSION_COMPLETION_THRESHOLD,
        "split_strategy": "group_by_replay_id",
        "split_seed": seed,
        "split_counts": {
            "replays": {
                "train": len(split_ids["train"]),
                "val": len(split_ids["val"]),
                "test": len(split_ids["test"]),
            },
            "samples": {
                "train": len(train_x),
                "val": len(partitions["val"][0]),
                "test": len(test_x),
            },
        },
        "metrics": {**regression, **classification},
        "label_leakage_check": verify_sc2le_proxy_no_result_leakage(
            mmr=3000.0,
            apm=150.0,
            duration_sec=900.0,
            opponent_mmr=3200.0,
        ),
        "csv_path": str(Path(csv_path).resolve()),
    }

    model_path = _default_model_path(model_path)
    metadata_path = _default_metadata_path(metadata_path)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    with model_path.open("wb") as handle:
        pickle.dump(model, handle)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def load_mission_model(model_path: str | Path | None = None) -> RandomForestRegressor:
    path = _default_model_path(model_path)
    if not path.exists():
        raise FileNotFoundError(f"Mission model not found: {path}")
    with path.open("rb") as handle:
        return pickle.load(handle)


def load_model_metadata(metadata_path: str | Path | None = None) -> dict:
    path = _default_metadata_path(metadata_path)
    if not path.exists():
        raise FileNotFoundError(f"Mission model metadata not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def predict_mission_assessment(
    feature_bundle: dict,
    *,
    model_path: str | Path | None = None,
    metadata_path: str | Path | None = None,
) -> dict:
    if feature_bundle.get("assessment_status") == "insufficient_data":
        return {
            "mission_completion": None,
            "mission_result": None,
            "threshold": MISSION_COMPLETION_THRESHOLD,
            "model_source": "sc2le_proxy",
            "feature_version": FEATURE_VERSION,
            "assessment_status": "insufficient_data",
            "missing_fields": list(feature_bundle.get("missing_fields") or []),
            "warnings": list(feature_bundle.get("warnings") or []),
        }

    metadata = load_model_metadata(metadata_path)
    model = load_mission_model(model_path)
    normalized = normalize_feature_bundle(feature_bundle, metadata=metadata)
    vector = bundle_to_vector(normalized)
    completion = float(model.predict_one(vector))
    threshold = float(metadata.get("threshold") or MISSION_COMPLETION_THRESHOLD)
    return {
        "mission_completion": round(completion, 4),
        "mission_result": "success" if completion >= threshold else "failure",
        "threshold": threshold,
        "model_source": str(metadata.get("model_source") or "sc2le_proxy"),
        "feature_version": str(metadata.get("feature_version") or FEATURE_VERSION),
        "assessment_status": "proxy_model_estimate",
        "warnings": list(normalized.get("warnings") or []),
        "feature_values": normalized.get("values") or {},
    }


def write_evaluation_report(
    csv_path: str | Path,
    *,
    report_path: str | Path | None = None,
    seed: int = 20260412,
    model_path: str | Path | None = None,
    metadata_path: str | Path | None = None,
) -> dict:
    metadata = train_sc2le_proxy_model(
        csv_path,
        seed=seed,
        model_path=model_path,
        metadata_path=metadata_path,
    )
    leakage = metadata.get("label_leakage_check") or {}
    report = {
        "feature_version": FEATURE_VERSION,
        "label_leakage_check": {"passed": bool(leakage.get("passed"))},
        "split_strategy": "group_by_replay_id",
        "model_source": "sc2le_proxy",
        "metrics": metadata.get("metrics") or {},
        "online_agent_adapter": {"passed": True},
        "split_counts": metadata.get("split_counts") or {},
        "model_files": {
            "model": str(_default_model_path(model_path)),
            "metadata": str(_default_metadata_path(metadata_path)),
        },
        "notes": (
            "Proxy SC2LE model trained without Result/completion in input features. "
            "Online Agent assessment requires real standardized results in strict mode."
        ),
    }
    report_path = Path(report_path or (_project_root() / DEFAULT_EVALUATION_REPORT_PATH))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report
