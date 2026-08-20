"""Frozen xBD damage classifier: handcrafted + optional ResNet18 embeddings."""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import pickle
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from .xbd_cnn_embedder import CNN_EMBEDDING_DIM, embed_roi_pair
from .xbd_feature_extraction import HANDCRAFTED_DIM, build_handcrafted_vector, normalize_polygon, polygon_features

DEFAULT_MODEL_PATH = "models/xbd_damage_classifier.pkl"
DEFAULT_MODEL_METADATA_PATH = "models/xbd_damage_classifier.metadata.json"
FEATURE_VERSION = "xbd_damage_v1"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_model_path(path: str | None = None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    return _repo_root() / DEFAULT_MODEL_PATH


def _default_metadata_path(path: str | None = None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    return _repo_root() / DEFAULT_MODEL_METADATA_PATH


def damage_label(value: Any) -> int:
    raw = str(value or "").strip().lower().replace("_", "-")
    if raw in {"un-classified", "unclassified"}:
        return -1
    if raw in {"1", "true", "yes", "damage", "damaged", "minor-damage", "major-damage", "destroyed"}:
        return 1
    if raw in {"0", "false", "no", "none", "no-damage"}:
        return 0
    try:
        return 1 if float(raw) >= 0.5 else 0
    except ValueError:
        return 0


def target_row_key(row: dict) -> str:
    sample_id = str(row.get("sample_id") or "").strip()
    building_index = row.get("building_index")
    if sample_id and building_index is not None and str(building_index) != "":
        return f"{sample_id}:{building_index}"
    target_id = str(row.get("target_id") or row.get("sample_id") or "").strip()
    if "-" in target_id:
        prefix, suffix = target_id.rsplit("-", 1)
        if suffix.isdigit():
            return f"{prefix}:{suffix}"
    return target_id


def load_cnn_embedding_store(npz_path: str) -> Tuple[Dict[str, List[float]], List[float], int]:
    if not npz_path or not os.path.exists(npz_path):
        return {}, [0.0] * CNN_EMBEDDING_DIM, CNN_EMBEDDING_DIM
    payload = np.load(npz_path, allow_pickle=True)
    keys = [str(item) for item in payload["keys"].tolist()]
    embeddings = payload["embeddings"].astype(float)
    store = {key: embeddings[index].tolist() for index, key in enumerate(keys)}
    mean_vector = embeddings.mean(axis=0).astype(float).tolist() if len(embeddings) else [0.0] * CNN_EMBEDDING_DIM
    dim = int(embeddings.shape[1]) if len(embeddings.shape) == 2 and embeddings.shape[0] else CNN_EMBEDDING_DIM
    return store, mean_vector, dim


def build_feature_vector(
    row: dict,
    *,
    cnn_store: Optional[Dict[str, List[float]]] = None,
    cnn_default: Optional[List[float]] = None,
    cnn_vector: Optional[Sequence[float]] = None,
    use_cnn: bool = True,
) -> List[float]:
    handcrafted = build_handcrafted_vector(row)
    if not use_cnn:
        return handcrafted
    if cnn_vector is not None:
        vector = [float(value) for value in cnn_vector]
    else:
        key = target_row_key(row)
        vector = (cnn_store or {}).get(key)
        if vector is None:
            vector = cnn_default or [0.0] * CNN_EMBEDDING_DIM
        vector = [float(value) for value in vector]
    return handcrafted + vector


class SklearnLogisticDamageModel:
    def __init__(self, estimator: Any, decision_threshold: float = 0.5) -> None:
        self.estimator = estimator
        self.decision_threshold = float(decision_threshold)

    def predict_proba_one(self, row: List[float]) -> float:
        prob = self.estimator.predict_proba(np.asarray([row], dtype=float))[0]
        return float(prob[1])

    def predict_one(self, row: List[float]) -> int:
        return 1 if self.predict_proba_one(row) >= self.decision_threshold else 0


def fit_sklearn_logistic(rows: List[List[float]], labels: List[int], seed: int) -> SklearnLogisticDamageModel:
    from sklearn.decomposition import PCA
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    width = len(rows[0]) if rows else 0
    pca_components = min(512, max(64, width // 3), max(1, len(rows) - 1))
    estimator = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("pca", PCA(n_components=pca_components, random_state=seed)),
            (
                "lr",
                LogisticRegression(
                    C=16.0,
                    class_weight="balanced",
                    max_iter=8000,
                    tol=1e-4,
                    solver="saga" if len(rows) > 5000 else "lbfgs",
                    random_state=seed,
                ),
            ),
        ]
    )
    estimator.fit(np.asarray(rows, dtype=float), np.asarray(labels, dtype=int))
    return SklearnLogisticDamageModel(estimator)


def stratified_split_with_ids(
    rows: List[List[float]],
    labels: List[int],
    sample_ids: Sequence[str],
    test_ratio: float,
    seed: int,
) -> Tuple[List[List[float]], List[int], List[List[float]], List[int], List[str], List[str]]:
    if not rows:
        return [], [], [], [], [], []
    ratio = max(0.05, min(0.45, test_ratio))
    pos_items = [(row, label, sample_id) for row, label, sample_id in zip(rows, labels, sample_ids) if int(label) == 1]
    neg_items = [(row, label, sample_id) for row, label, sample_id in zip(rows, labels, sample_ids) if int(label) == 0]
    rng = random.Random(seed)
    rng.shuffle(pos_items)
    rng.shuffle(neg_items)
    test_pos = max(1, int(len(pos_items) * ratio)) if pos_items else 0
    test_neg = max(1, int(len(neg_items) * ratio)) if neg_items else 0
    test_items = pos_items[:test_pos] + neg_items[:test_neg]
    train_items = pos_items[test_pos:] + neg_items[test_neg:]
    rng.shuffle(train_items)
    rng.shuffle(test_items)
    return (
        [list(item[0]) for item in train_items],
        [int(item[1]) for item in train_items],
        [list(item[0]) for item in test_items],
        [int(item[1]) for item in test_items],
        [str(item[2]) for item in train_items],
        [str(item[2]) for item in test_items],
    )


def subsample_stratified_with_ids(
    rows: List[List[float]],
    labels: List[int],
    sample_ids: Sequence[str],
    max_samples: int,
    seed: int,
) -> Tuple[List[List[float]], List[int], List[str]]:
    if len(rows) <= max_samples:
        return rows, labels, list(sample_ids)
    pos_idx = [idx for idx, label in enumerate(labels) if int(label) == 1]
    neg_idx = [idx for idx, label in enumerate(labels) if int(label) == 0]
    rng = random.Random(seed)
    rng.shuffle(pos_idx)
    rng.shuffle(neg_idx)
    pos_ratio = len(pos_idx) / max(1, len(labels))
    pos_take = max(1, int(max_samples * pos_ratio))
    neg_take = max(1, max_samples - pos_take)
    chosen = pos_idx[: min(pos_take, len(pos_idx))] + neg_idx[: min(neg_take, len(neg_idx))]
    if len(chosen) < max_samples:
        remaining = [idx for idx in range(len(rows)) if idx not in chosen]
        rng.shuffle(remaining)
        chosen.extend(remaining[: max_samples - len(chosen)])
    rng.shuffle(chosen)
    return [rows[idx] for idx in chosen], [labels[idx] for idx in chosen], [str(sample_ids[idx]) for idx in chosen]


def tune_decision_threshold(probs: List[float], labels: List[int]) -> float:
    if not probs:
        return 0.5
    best_threshold = 0.5
    best_score = -1.0
    for step in range(1, 200):
        threshold = step / 200.0
        preds = [1 if prob >= threshold else 0 for prob in probs]
        accuracy = sum(pred == label for pred, label in zip(preds, labels)) / len(labels)
        tp = sum(1 for pred, label in zip(preds, labels) if pred == 1 and label == 1)
        fn = sum(1 for pred, label in zip(preds, labels) if pred == 0 and label == 1)
        fp = sum(1 for pred, label in zip(preds, labels) if pred == 1 and label == 0)
        tn = sum(1 for pred, label in zip(preds, labels) if pred == 0 and label == 0)
        recall = tp / max(1, tp + fn)
        precision = tp / max(1, tp + fp)
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
        specificity = tn / max(1, tn + fp)
        balanced = 0.5 * (recall + specificity)
        score = 0.92 * accuracy + 0.05 * balanced + 0.03 * f1
        if score > best_score:
            best_score = score
            best_threshold = threshold
    return best_threshold


def classification_metrics(probs: List[float], labels: List[int], threshold: float) -> dict:
    preds = [1 if prob >= threshold else 0 for prob in probs]
    tp = sum(1 for pred, label in zip(preds, labels) if pred == 1 and label == 1)
    fn = sum(1 for pred, label in zip(preds, labels) if pred == 0 and label == 1)
    fp = sum(1 for pred, label in zip(preds, labels) if pred == 1 and label == 0)
    tn = sum(1 for pred, label in zip(preds, labels) if pred == 0 and label == 0)
    total = max(1, len(labels))
    accuracy = (tp + tn) / total
    recall = tp / max(1, tp + fn)
    precision = tp / max(1, tp + fp)
    specificity = tn / max(1, tn + fp)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    balanced = 0.5 * (recall + specificity)
    return {
        "damage_accuracy": accuracy,
        "damage_f1": f1,
        "damage_balanced_accuracy": balanced,
        "damage_precision": precision,
        "damage_recall": recall,
        "decision_threshold": threshold,
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
    }


def read_feature_rows(path: str) -> List[dict]:
    rows: List[dict] = []
    if path.lower().endswith(".jsonl"):
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if isinstance(payload, dict):
                    rows.append(payload)
        return rows
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def load_feature_table(
    path: str,
    *,
    cnn_store: Optional[Dict[str, List[float]]] = None,
    cnn_default: Optional[List[float]] = None,
    require_cnn: bool = False,
    use_cnn: bool = True,
) -> Tuple[List[List[float]], List[int], List[str]]:
    rows: List[List[float]] = []
    labels: List[int] = []
    sample_ids: List[str] = []
    for row in read_feature_rows(path):
        subtype = str(row.get("subtype") or "").strip().lower()
        if subtype in {"un-classified", "unclassified"}:
            continue
        label_value = row.get("damage_label") or row.get("subtype") or row.get("damage_class") or row.get("label")
        label = damage_label(label_value)
        if label < 0:
            continue
        sample_id = str(row.get("sample_id") or row.get("target_id") or len(sample_ids))
        row["sample_id"] = sample_id
        row_key = target_row_key(row)
        if require_cnn and use_cnn and cnn_store and row_key not in cnn_store:
            continue
        rows.append(build_feature_vector(row, cnn_store=cnn_store, cnn_default=cnn_default, use_cnn=use_cnn))
        labels.append(label)
        sample_ids.append(sample_id)
    return rows, labels, sample_ids


def train_damage_classifier(
    feature_csv: str,
    *,
    cnn_npz: str = "",
    seed: int = 20260623,
    max_fit_samples: int = 100000,
    model_path: str | Path | None = None,
    metadata_path: str | Path | None = None,
) -> dict:
    cnn_path = cnn_npz.strip()
    if not cnn_path and feature_csv:
        cnn_path = str(Path(feature_csv).with_name("xbd_cnn_embeddings_train.npz"))
    cnn_store, cnn_default, cnn_dim = load_cnn_embedding_store(cnn_path)
    use_cnn = bool(cnn_store)
    x_rows, y_labels, sample_ids = load_feature_table(
        feature_csv,
        cnn_store=cnn_store,
        cnn_default=cnn_default,
        require_cnn=use_cnn,
        use_cnn=use_cnn,
    )
    if len(x_rows) < 8 or len(set(y_labels)) < 2:
        raise RuntimeError("insufficient labeled rows for xBD damage classifier training")

    paired = list(zip(x_rows, y_labels, sample_ids))
    random.Random(seed).shuffle(paired)
    x_shuffled = [list(item[0]) for item in paired]
    y_shuffled = [int(item[1]) for item in paired]
    ids_shuffled = [str(item[2]) for item in paired]
    x_train, y_train, x_test, y_test, ids_train, ids_test = stratified_split_with_ids(
        x_shuffled,
        y_shuffled,
        ids_shuffled,
        0.2,
        seed,
    )
    x_fit, y_fit, x_val, y_val, _, _ = stratified_split_with_ids(
        x_train,
        y_train,
        ids_train,
        0.2,
        seed + 1,
    )
    if len(x_fit) > max_fit_samples:
        x_fit, y_fit, _ = subsample_stratified_with_ids(x_fit, y_fit, ids_train, max_fit_samples, seed + 9)

    model = fit_sklearn_logistic(x_fit, y_fit, seed)
    val_probs = [model.predict_proba_one(row) for row in x_val]
    decision_threshold = tune_decision_threshold(val_probs, y_val)
    model.decision_threshold = float(decision_threshold)
    test_probs = [model.predict_proba_one(row) for row in x_test]
    metrics = classification_metrics(test_probs, y_test, decision_threshold)

    model_out = _default_model_path(str(model_path) if model_path else None)
    metadata_out = _default_metadata_path(str(metadata_path) if metadata_path else None)
    model_out.parent.mkdir(parents=True, exist_ok=True)
    with model_out.open("wb") as handle:
        pickle.dump(model, handle)

    metadata = {
        "model_source": "xbd_handcrafted_plus_resnet18_lr" if use_cnn else "xbd_handcrafted_lr",
        "feature_version": FEATURE_VERSION,
        "handcrafted_dim": HANDCRAFTED_DIM,
        "cnn_dim": cnn_dim if use_cnn else 0,
        "input_dim": len(x_rows[0]),
        "use_cnn": use_cnn,
        "decision_threshold": decision_threshold,
        "split_seed": seed,
        "split_counts": {
            "fit": len(x_fit),
            "val": len(x_val),
            "test": len(x_test),
            "total": len(x_rows),
        },
        "metrics": metrics,
        "feature_csv": str(Path(feature_csv).resolve()),
        "cnn_npz": str(Path(cnn_path).resolve()) if use_cnn else "",
    }
    metadata_out.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "model_path": str(model_out),
        "metadata_path": str(metadata_out),
        "use_cnn": use_cnn,
        "metrics": metrics,
        "split_counts": metadata["split_counts"],
    }


def load_damage_model(model_path: str | Path | None = None) -> SklearnLogisticDamageModel:
    path = _default_model_path(str(model_path) if model_path is not None else None)
    if not path.exists():
        raise FileNotFoundError(f"xBD damage model not found: {path}")
    with path.open("rb") as handle:
        return pickle.load(handle)


def load_model_metadata(metadata_path: str | Path | None = None) -> dict:
    path = _default_metadata_path(str(metadata_path) if metadata_path is not None else None)
    if not path.exists():
        raise FileNotFoundError(f"xBD damage model metadata not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def damage_model_loaded(
    model_path: str | Path | None = None,
    metadata_path: str | Path | None = None,
) -> bool:
    return _default_model_path(str(model_path) if model_path else None).exists() and _default_metadata_path(
        str(metadata_path) if metadata_path else None
    ).exists()


def decode_image_payload(payload: Any) -> Image.Image:
    if isinstance(payload, dict):
        if payload.get("path"):
            return Image.open(str(payload["path"])).convert("RGB")
        payload = payload.get("base64") or payload.get("data") or payload.get("content")
    if not isinstance(payload, str):
        raise ValueError("image payload must be a base64 string or {path: ...}")
    text = payload.strip()
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    raw = base64.b64decode(text)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def assess_damage(
    inputs: dict,
    *,
    model_path: str | Path | None = None,
    metadata_path: str | Path | None = None,
    device: str = "cpu",
) -> dict:
    input_mode = str(inputs.get("input_mode") or "features").strip().lower()
    metadata = load_model_metadata(metadata_path)
    model = load_damage_model(model_path)
    threshold = float(metadata.get("decision_threshold") or getattr(model, "decision_threshold", 0.5))
    use_cnn = bool(metadata.get("use_cnn", True))
    sample_id = str(inputs.get("sample_id") or inputs.get("target_id") or "")

    if input_mode == "features":
        handcrafted = dict(inputs.get("handcrafted_features") or inputs.get("features") or {})
        if not handcrafted:
            return {
                "assessment_status": "insufficient_data",
                "missing_fields": ["handcrafted_features"],
                "damage_probability": None,
                "damage_label": None,
                "decision_threshold": threshold,
                "model_source": metadata.get("model_source"),
                "feature_version": metadata.get("feature_version", FEATURE_VERSION),
            }
        row = dict(handcrafted)
        if sample_id:
            row["sample_id"] = sample_id
        cnn_vector = inputs.get("cnn_embedding")
        if cnn_vector is None and use_cnn:
            cnn_vector = [0.0] * int(metadata.get("cnn_dim") or CNN_EMBEDDING_DIM)
        vector = build_feature_vector(
            row,
            cnn_vector=cnn_vector if use_cnn else None,
            use_cnn=use_cnn,
        )
        source = "features"
    elif input_mode == "images":
        polygon = inputs.get("polygon")
        if polygon is None or polygon == "" or polygon == []:
            return {
                "assessment_status": "insufficient_data",
                "missing_fields": ["polygon"],
                "damage_probability": None,
                "damage_label": None,
                "decision_threshold": threshold,
                "model_source": metadata.get("model_source"),
                "feature_version": metadata.get("feature_version", FEATURE_VERSION),
                "warnings": ["images mode requires polygon; bbox fallback is disabled"],
            }
        pre_image = decode_image_payload(inputs.get("pre_image"))
        post_image = decode_image_payload(inputs.get("post_image"))
        points = normalize_polygon(polygon)
        if not points:
            return {
                "assessment_status": "insufficient_data",
                "missing_fields": ["polygon"],
                "damage_probability": None,
                "damage_label": None,
                "decision_threshold": threshold,
                "model_source": metadata.get("model_source"),
                "feature_version": metadata.get("feature_version", FEATURE_VERSION),
                "warnings": ["images mode requires a valid polygon with at least 3 points"],
            }
        roi = polygon_features(pre_image, post_image, points)
        if roi is None:
            return {
                "assessment_status": "insufficient_data",
                "missing_fields": ["valid_polygon_roi"],
                "damage_probability": None,
                "damage_label": None,
                "decision_threshold": threshold,
                "model_source": metadata.get("model_source"),
                "feature_version": metadata.get("feature_version", FEATURE_VERSION),
            }
        row = dict(roi)
        if sample_id:
            row["sample_id"] = sample_id
        cnn_vector = None
        if use_cnn:
            cnn_vector = embed_roi_pair(pre_image, post_image, polygon, device=device)
            if cnn_vector is None:
                return {
                    "assessment_status": "insufficient_data",
                    "missing_fields": ["valid_polygon_roi"],
                    "damage_probability": None,
                    "damage_label": None,
                    "decision_threshold": threshold,
                    "model_source": metadata.get("model_source"),
                    "feature_version": metadata.get("feature_version", FEATURE_VERSION),
                }
        vector = build_feature_vector(row, cnn_vector=cnn_vector, use_cnn=use_cnn)
        source = "images"
    else:
        raise ValueError('input_mode must be "features" or "images"')

    probability = float(model.predict_proba_one(vector))
    label = 1 if probability >= threshold else 0
    return {
        "damage_probability": round(probability, 6),
        "damage_label": label,
        "damage_result": "damaged" if label == 1 else "no_damage",
        "decision_threshold": threshold,
        "model_source": metadata.get("model_source"),
        "feature_version": metadata.get("feature_version", FEATURE_VERSION),
        "assessment_status": "model_estimate",
        "input_mode": source,
        "use_cnn": use_cnn,
        "feature_dim": len(vector),
    }
