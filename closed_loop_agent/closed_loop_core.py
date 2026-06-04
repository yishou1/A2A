#!/usr/bin/env python3
import csv
import json
import math
import os
import random
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

MCP_PROTOCOL_VERSION = "2024-11-05"


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


class StandardScaler:
    def __init__(self) -> None:
        self.means: List[float] = []
        self.stds: List[float] = []

    def fit(self, rows: List[List[float]]) -> "StandardScaler":
        if not rows:
            self.means = []
            self.stds = []
            return self
        width = len(rows[0])
        self.means = [_mean([row[i] for row in rows]) for i in range(width)]
        self.stds = []
        for i in range(width):
            variance = _mean([(row[i] - self.means[i]) ** 2 for row in rows])
            self.stds.append(math.sqrt(variance) or 1.0)
        return self

    def transform_one(self, row: List[float]) -> List[float]:
        return [(row[i] - self.means[i]) / self.stds[i] for i in range(len(row))]

    def transform(self, rows: List[List[float]]) -> List[List[float]]:
        return [self.transform_one(row) for row in rows]


class LogisticRegressionGD:
    def __init__(self, learning_rate: float = 0.28, iterations: int = 420, l2: float = 0.001) -> None:
        self.learning_rate = learning_rate
        self.iterations = iterations
        self.l2 = l2
        self.scaler = StandardScaler()
        self.weights: List[float] = []
        self.bias = 0.0

    def fit(self, rows: List[List[float]], labels: List[int]) -> "LogisticRegressionGD":
        x_rows = self.scaler.fit(rows).transform(rows)
        width = len(x_rows[0]) if x_rows else 0
        self.weights = [0.0 for _ in range(width)]
        self.bias = 0.0
        n = max(1, len(x_rows))
        for _ in range(self.iterations):
            grad_w = [0.0 for _ in range(width)]
            grad_b = 0.0
            for row, label in zip(x_rows, labels):
                pred = _sigmoid(sum(w * v for w, v in zip(self.weights, row)) + self.bias)
                err = pred - float(label)
                for i, value in enumerate(row):
                    grad_w[i] += err * value
                grad_b += err
            for i in range(width):
                grad = (grad_w[i] / n) + self.l2 * self.weights[i]
                self.weights[i] -= self.learning_rate * grad
            self.bias -= self.learning_rate * (grad_b / n)
        return self

    def predict_proba_one(self, row: List[float]) -> float:
        x_row = self.scaler.transform_one(row)
        return _sigmoid(sum(w * v for w, v in zip(self.weights, x_row)) + self.bias)

    def predict_proba(self, rows: List[List[float]]) -> List[float]:
        return [self.predict_proba_one(row) for row in rows]


class KMeans:
    def __init__(self, k: int = 3, iterations: int = 40, seed: int = 7) -> None:
        self.k = k
        self.iterations = iterations
        self.rng = random.Random(seed)
        self.scaler = StandardScaler()
        self.centroids: List[List[float]] = []

    @staticmethod
    def _distance(a: List[float], b: List[float]) -> float:
        return sum((x - y) ** 2 for x, y in zip(a, b))

    def fit(self, rows: List[List[float]]) -> "KMeans":
        x_rows = self.scaler.fit(rows).transform(rows)
        if not x_rows:
            self.centroids = []
            return self
        initial = self.rng.sample(x_rows, min(self.k, len(x_rows)))
        while len(initial) < self.k:
            initial.append(x_rows[len(initial) % len(x_rows)])
        self.centroids = [list(row) for row in initial]
        for _ in range(self.iterations):
            buckets: List[List[List[float]]] = [[] for _ in range(self.k)]
            for row in x_rows:
                label = self._closest(row)
                buckets[label].append(row)
            for idx, bucket in enumerate(buckets):
                if not bucket:
                    self.centroids[idx] = list(x_rows[self.rng.randrange(len(x_rows))])
                    continue
                width = len(bucket[0])
                self.centroids[idx] = [_mean([row[i] for row in bucket]) for i in range(width)]
        return self

    def _closest(self, row: List[float]) -> int:
        distances = [self._distance(row, centroid) for centroid in self.centroids]
        return min(range(len(distances)), key=lambda idx: distances[idx])

    def predict(self, rows: List[List[float]]) -> List[int]:
        x_rows = self.scaler.transform(rows)
        return [self._closest(row) for row in x_rows]


class RegressionTree:
    def __init__(self, max_depth: int = 6, min_leaf: int = 8, max_features: Optional[int] = None, seed: int = 11) -> None:
        self.max_depth = max_depth
        self.min_leaf = min_leaf
        self.max_features = max_features
        self.rng = random.Random(seed)
        self.root: dict = {}

    def fit(self, rows: List[List[float]], targets: List[float]) -> "RegressionTree":
        self.root = self._build(rows, targets, depth=0)
        return self

    def _leaf(self, targets: List[float]) -> dict:
        return {"leaf": True, "value": _mean(targets)}

    def _sse(self, values: List[float]) -> float:
        if not values:
            return 0.0
        avg = _mean(values)
        return sum((value - avg) ** 2 for value in values)

    def _feature_candidates(self, width: int) -> List[int]:
        features = list(range(width))
        self.rng.shuffle(features)
        if self.max_features is None:
            return features
        return features[: max(1, min(width, self.max_features))]

    def _thresholds(self, rows: List[List[float]], feature: int) -> List[float]:
        values = sorted({row[feature] for row in rows})
        if len(values) <= 1:
            return []
        if len(values) <= 12:
            return [(values[i] + values[i + 1]) / 2.0 for i in range(len(values) - 1)]
        picks = []
        for q in (0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9):
            pos = min(len(values) - 2, max(0, int(q * (len(values) - 1))))
            picks.append((values[pos] + values[pos + 1]) / 2.0)
        return sorted(set(picks))

    def _best_split(self, rows: List[List[float]], targets: List[float]) -> Optional[Tuple[int, float]]:
        width = len(rows[0]) if rows else 0
        best_score = float("inf")
        best: Optional[Tuple[int, float]] = None
        for feature in self._feature_candidates(width):
            for threshold in self._thresholds(rows, feature):
                left_y: List[float] = []
                right_y: List[float] = []
                for row, target in zip(rows, targets):
                    if row[feature] <= threshold:
                        left_y.append(target)
                    else:
                        right_y.append(target)
                if len(left_y) < self.min_leaf or len(right_y) < self.min_leaf:
                    continue
                score = self._sse(left_y) + self._sse(right_y)
                if score < best_score:
                    best_score = score
                    best = (feature, threshold)
        return best

    def _build(self, rows: List[List[float]], targets: List[float], depth: int) -> dict:
        if depth >= self.max_depth or len(rows) < self.min_leaf * 2 or self._sse(targets) < 1e-6:
            return self._leaf(targets)
        split = self._best_split(rows, targets)
        if split is None:
            return self._leaf(targets)
        feature, threshold = split
        left_x: List[List[float]] = []
        left_y: List[float] = []
        right_x: List[List[float]] = []
        right_y: List[float] = []
        for row, target in zip(rows, targets):
            if row[feature] <= threshold:
                left_x.append(row)
                left_y.append(target)
            else:
                right_x.append(row)
                right_y.append(target)
        return {
            "leaf": False,
            "feature": feature,
            "threshold": threshold,
            "left": self._build(left_x, left_y, depth + 1),
            "right": self._build(right_x, right_y, depth + 1),
        }

    def predict_one(self, row: List[float]) -> float:
        node = self.root
        while not node.get("leaf"):
            feature = int(node["feature"])
            threshold = float(node["threshold"])
            node = node["left"] if row[feature] <= threshold else node["right"]
        return float(node.get("value", 0.0))


class RandomForestRegressor:
    def __init__(self, trees: int = 19, max_depth: int = 6, min_leaf: int = 8, seed: int = 17) -> None:
        self.trees = trees
        self.max_depth = max_depth
        self.min_leaf = min_leaf
        self.seed = seed
        self.scaler = StandardScaler()
        self.models: List[RegressionTree] = []

    def fit(self, rows: List[List[float]], targets: List[float]) -> "RandomForestRegressor":
        x_rows = self.scaler.fit(rows).transform(rows)
        rng = random.Random(self.seed)
        width = len(x_rows[0]) if x_rows else 0
        max_features = max(1, int(math.sqrt(width)))
        self.models = []
        for index in range(self.trees):
            sample_x: List[List[float]] = []
            sample_y: List[float] = []
            for _ in range(len(x_rows)):
                pos = rng.randrange(len(x_rows))
                sample_x.append(x_rows[pos])
                sample_y.append(targets[pos])
            tree = RegressionTree(self.max_depth, self.min_leaf, max_features=max_features, seed=self.seed + index)
            tree.fit(sample_x, sample_y)
            self.models.append(tree)
        return self

    def predict_one(self, row: List[float]) -> float:
        x_row = self.scaler.transform_one(row)
        return _clamp(_mean([tree.predict_one(x_row) for tree in self.models]))

    def predict(self, rows: List[List[float]]) -> List[float]:
        return [self.predict_one(row) for row in rows]


def _split(rows: List[List[float]], labels: List[Any], test_count: int) -> Tuple[List[List[float]], List[Any], List[List[float]], List[Any]]:
    test_count = min(test_count, max(1, len(rows) // 3))
    return rows[:-test_count], labels[:-test_count], rows[-test_count:], labels[-test_count:]


def _split_shuffled(
    rows: List[List[float]],
    labels: List[Any],
    test_count: int,
    seed: int,
) -> Tuple[List[List[float]], List[Any], List[List[float]], List[Any]]:
    paired = list(zip(rows, labels))
    random.Random(seed).shuffle(paired)
    rows_s = [list(row) for row, _ in paired]
    labels_s = [label for _, label in paired]
    return _split(rows_s, labels_s, test_count)


def _as_float(row: dict, names: Sequence[str], default: float = 0.0) -> float:
    for name in names:
        value = row.get(name)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def _damage_label(value: Any) -> int:
    raw = str(value or "").strip().lower().replace("_", "-")
    if raw in {"1", "true", "yes", "damage", "damaged", "minor-damage", "major-damage", "destroyed"}:
        return 1
    if raw in {"0", "false", "no", "none", "no-damage", "un-classified", "unclassified"}:
        return 0
    try:
        return 1 if float(raw) >= 0.5 else 0
    except ValueError:
        return 0


def _read_rows(path: str) -> List[dict]:
    if not path or not os.path.exists(path):
        return []
    ext = os.path.splitext(path)[1].lower()
    if ext in {".jsonl", ".ndjson"}:
        rows: List[dict] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if isinstance(item, dict):
                    rows.append(item)
        return rows
    if ext == ".json":
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("rows", "samples", "features", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def _load_xbd_feature_rows(path: str) -> Tuple[List[List[float]], List[int]]:
    rows: List[List[float]] = []
    labels: List[int] = []
    for row in _read_rows(path):
        label_value = (
            row.get("damage_label")
            or row.get("damage_class")
            or row.get("subtype")
            or row.get("label")
            or row.get("damage_confirmed")
            or row.get("target")
            or row.get("y")
        )
        rows.append(
            [
                _as_float(row, ["pre_area", "area_norm", "building_area_norm", "area"], 0.5),
                _as_float(row, ["spectral_delta", "delta_spectral", "mean_abs_diff", "change_score"], 0.0),
                _as_float(row, ["texture_delta", "delta_texture", "edge_change"], 0.0),
                _as_float(row, ["heat_signature", "thermal_delta", "brightness_delta"], 0.0),
                _as_float(row, ["crater_density", "debris_density", "damage_texture"], 0.0),
                _as_float(row, ["normalized_distance", "distance_norm", "distance_to_center"], 0.5),
                _as_float(row, ["detection_confidence", "det_conf", "confidence"], 0.8),
                _as_float(row, ["threat_score", "priority_score", "prior_threat"], 0.5),
            ]
        )
        labels.append(_damage_label(label_value))
    return rows, labels


def _load_sc2le_feature_rows(path: str) -> Tuple[List[List[float]], List[float]]:
    rows: List[List[float]] = []
    targets: List[float] = []
    for row in _read_rows(path):
        target = _as_float(row, ["task_completion", "completion", "completion_score", "win_score", "score"], 0.0)
        rows.append(
            [
                _as_float(row, ["damage_rate", "enemy_destroyed_rate", "combat_efficiency"], 0.0),
                _as_float(row, ["asset_readiness", "friendly_readiness", "unit_health_ratio"], 0.7),
                _as_float(row, ["control_timeliness", "action_timeliness", "apm_norm"], 0.7),
                _as_float(row, ["intel_confidence", "visibility_ratio", "observation_confidence"], 0.7),
                _as_float(row, ["threat_pressure", "enemy_pressure", "risk"], 0.5),
                _as_float(row, ["ammo_pressure", "resource_pressure", "supply_pressure"], 0.5),
                _as_float(row, ["comm_quality", "coordination_score", "team_sync"], 0.8),
            ]
        )
        targets.append(_clamp(target))
    return rows, targets


def _dataset_paths(arguments: dict) -> dict:
    paths = _safe_dict(arguments.get("dataset_paths"))
    if not paths:
        paths = _safe_dict(arguments.get("datasets"))
    return {
        "xbd_damage_csv": str(paths.get("xbd_damage_csv") or paths.get("xbd_damage_jsonl") or paths.get("xbd_damage_path") or "").strip(),
        "sc2le_task_csv": str(paths.get("sc2le_task_csv") or paths.get("sc2le_task_jsonl") or paths.get("sc2le_task_path") or "").strip(),
    }


def _generate_xbd_like_damage_data(n: int, seed: int) -> Tuple[List[List[float]], List[int]]:
    rng = random.Random(seed)
    rows: List[List[float]] = []
    labels: List[int] = []
    for _ in range(n):
        distance = rng.random()
        cover = rng.random()
        munition_effect = rng.random()
        pre_area = rng.uniform(0.25, 1.0)
        spectral_delta = _clamp(0.18 + 0.72 * munition_effect - 0.24 * cover + rng.gauss(0, 0.045))
        texture_delta = _clamp(0.12 + 0.68 * munition_effect - 0.18 * cover + rng.gauss(0, 0.05))
        heat_signature = _clamp(0.08 + 0.78 * munition_effect - 0.16 * distance + rng.gauss(0, 0.05))
        crater_density = _clamp(0.10 + 0.64 * munition_effect - 0.22 * distance + rng.gauss(0, 0.04))
        detection_conf = _clamp(0.62 + 0.28 * (1.0 - distance) + rng.gauss(0, 0.03))
        prior_threat = rng.random()
        severity = (
            1.15 * spectral_delta
            + 0.95 * texture_delta
            + 0.90 * heat_signature
            + 0.80 * crater_density
            + 0.25 * detection_conf
            - 0.35 * distance
            - 0.20 * cover
            + rng.gauss(0, 0.08)
        )
        label = 1 if severity >= 1.38 else 0
        rows.append([pre_area, spectral_delta, texture_delta, heat_signature, crater_density, distance, detection_conf, prior_threat])
        labels.append(label)
    return rows, labels


def _generate_sc2le_like_task_data(n: int, seed: int) -> Tuple[List[List[float]], List[float]]:
    rng = random.Random(seed)
    rows: List[List[float]] = []
    targets: List[float] = []
    for _ in range(n):
        damage_rate = rng.random()
        asset_readiness = rng.uniform(0.45, 1.0)
        control_timeliness = rng.uniform(0.55, 1.0)
        intel_confidence = rng.uniform(0.50, 1.0)
        threat_pressure = rng.random()
        ammo_pressure = rng.random()
        comm_quality = rng.uniform(0.45, 1.0)
        completion = (
            0.12
            + 0.38 * damage_rate
            + 0.22 * asset_readiness
            + 0.14 * control_timeliness
            + 0.12 * intel_confidence
            + 0.10 * comm_quality
            - 0.18 * threat_pressure
            - 0.10 * ammo_pressure
            + rng.gauss(0, 0.035)
        )
        rows.append([damage_rate, asset_readiness, control_timeliness, intel_confidence, threat_pressure, ammo_pressure, comm_quality])
        targets.append(_clamp(completion))
    return rows, targets


def _train_models(seed: int, paths: Optional[dict] = None) -> dict:
    paths = paths if isinstance(paths, dict) else {}
    data_sources = {
        "damage_assessment": {"kind": "simulated", "path": "", "samples": 0},
        "mission_evaluation": {"kind": "simulated", "path": "", "samples": 0},
    }

    xbd_path = str(paths.get("xbd_damage_csv") or "").strip()
    xbd_x, xbd_y = _load_xbd_feature_rows(xbd_path) if xbd_path else ([], [])
    if len(xbd_x) >= 8 and len(set(xbd_y)) >= 2:
        data_sources["damage_assessment"] = {"kind": "real_feature_table", "path": xbd_path, "samples": len(xbd_x)}
        x_train, y_train, x_test, y_test = _split_shuffled(xbd_x, xbd_y, max(1, len(xbd_x) // 5), seed)
    else:
        xbd_x, xbd_y = _generate_xbd_like_damage_data(780, seed)
        x_train, y_train, x_test, y_test = _split(xbd_x, xbd_y, 180)
    damage_model = LogisticRegressionGD().fit(x_train, [int(y) for y in y_train])
    probs = damage_model.predict_proba(x_test)
    preds = [1 if prob >= 0.5 else 0 for prob in probs]
    damage_accuracy = sum(1 for pred, label in zip(preds, y_test) if pred == label) / len(y_test)

    cluster_x = [[row[7], 1.0 - row[5], row[6], row[1], row[3]] for row in xbd_x[:360]]
    kmeans = KMeans(k=3, seed=seed + 1).fit(cluster_x)

    sc2_path = str(paths.get("sc2le_task_csv") or "").strip()
    sc2_x, sc2_y = _load_sc2le_feature_rows(sc2_path) if sc2_path else ([], [])
    if len(sc2_x) >= 50:
        data_sources["mission_evaluation"] = {"kind": "real_feature_table", "path": sc2_path, "samples": len(sc2_x)}
        sc_train_x, sc_train_y, sc_test_x, sc_test_y = _split_shuffled(sc2_x, sc2_y, max(1, len(sc2_x) // 5), seed + 2)
    else:
        sc2_x, sc2_y = _generate_sc2le_like_task_data(760, seed + 2)
        sc_train_x, sc_train_y, sc_test_x, sc_test_y = _split(sc2_x, sc2_y, 180)
    mission_model = RandomForestRegressor(seed=seed + 3).fit(sc_train_x, [float(y) for y in sc_train_y])
    mission_preds = mission_model.predict(sc_test_x)
    mae = _mean([abs(pred - truth) for pred, truth in zip(mission_preds, sc_test_y)])
    task_completion_accuracy = 1.0 - mae
    truth_mean = _mean(sc_test_y)
    ss_tot = sum((truth - truth_mean) ** 2 for truth in sc_test_y) or 1.0
    ss_res = sum((pred - truth) ** 2 for pred, truth in zip(mission_preds, sc_test_y))
    r2 = 1.0 - ss_res / ss_tot
    return {
        "damage_model": damage_model,
        "kmeans": kmeans,
        "mission_model": mission_model,
        "metrics": {
            "damage_accuracy": damage_accuracy,
            "task_completion_accuracy": task_completion_accuracy,
            "task_completion_mae": mae,
            "task_completion_r2": r2,
        },
        "data_sources": data_sources,
    }


def _extract_upstream_results(arguments: dict) -> dict:
    results = _safe_dict(arguments.get("results"))
    if results:
        return results
    result = _safe_dict(arguments.get("result"))
    if result:
        return {str(result.get("task_type") or "upstream"): result}
    previous = _safe_dict(arguments.get("previous_results"))
    if previous:
        return previous
    blackboard = _safe_dict(arguments.get("blackboard"))
    memory = _safe_dict(blackboard.get("memory"))
    return _safe_dict(memory.get("results_by_task"))


def _base_from_upstream(results: dict) -> dict:
    perception = _safe_dict(results.get("perception_detection"))
    recognition = _safe_dict(results.get("recognition"))
    fusion = _safe_dict(results.get("data_fusion"))
    threat = _safe_dict(results.get("threat_evaluation"))

    perception_out = _safe_dict(perception.get("output_data"))
    recognition_out = _safe_dict(recognition.get("output_data"))
    fusion_out = _safe_dict(fusion.get("output_data"))
    threat_out = _safe_dict(threat.get("output_data"))
    detections = _safe_list(perception_out.get("detections"))
    first_det = _safe_dict(detections[0]) if detections else {}
    fused_track = _safe_dict(fusion_out.get("fused_track"))

    return {
        "det_conf": float(first_det.get("conf") or fused_track.get("det_conf") or 0.82),
        "class_conf": float(recognition_out.get("confidence") or fused_track.get("class_confidence") or 0.86),
        "threat_score": float(threat_out.get("priority_score") or 0.70),
        "target_class": str(recognition_out.get("target_class") or fused_track.get("target_class") or "Unknown"),
        "track_id": str(fused_track.get("track_id") or perception_out.get("frame_id") or "track"),
    }


def _build_live_targets(arguments: dict, seed: int) -> Tuple[List[dict], dict]:
    rng = random.Random(seed + 10)
    requested = int(arguments.get("target_count") or 50)
    enforce_min_target_count = bool(arguments.get("enforce_min_target_count", True))
    min_target_count = 50 if enforce_min_target_count else 1
    target_count = max(min_target_count, requested)
    explicit_targets = _safe_list(arguments.get("targets"))
    if explicit_targets:
        target_count = max(min_target_count, len(explicit_targets))
    results = _extract_upstream_results(arguments)
    base = _base_from_upstream(results)
    targets: List[dict] = []
    for index in range(target_count):
        explicit = _safe_dict(explicit_targets[index]) if index < len(explicit_targets) else {}
        det_conf = _clamp(float(explicit.get("detection_confidence", base["det_conf"])) + rng.gauss(0, 0.035))
        threat_score = _clamp(float(explicit.get("threat_score", base["threat_score"])) + rng.gauss(0, 0.12))
        initial_effect = _clamp(float(explicit.get("initial_effect", 0.38 + 0.28 * rng.random())))
        distance = _clamp(float(explicit.get("normalized_distance", rng.random())))
        target = {
            "target_id": str(explicit.get("target_id") or f"{base['track_id']}-{index + 1:03d}"),
            "target_class": str(explicit.get("target_class") or base["target_class"]),
            "pre_area": _clamp(float(explicit.get("pre_area", rng.uniform(0.35, 1.0)))),
            "spectral_delta": _clamp(float(explicit.get("spectral_delta", initial_effect + rng.gauss(0, 0.08)))),
            "texture_delta": _clamp(float(explicit.get("texture_delta", initial_effect * 0.90 + rng.gauss(0, 0.08)))),
            "heat_signature": _clamp(float(explicit.get("heat_signature", initial_effect * 0.85 + rng.gauss(0, 0.09)))),
            "crater_density": _clamp(float(explicit.get("crater_density", initial_effect * 0.72 + rng.gauss(0, 0.08)))),
            "normalized_distance": distance,
            "detection_confidence": det_conf,
            "threat_score": threat_score,
            "velocity_norm": _clamp(float(explicit.get("velocity_norm", rng.random()))),
            "uncertainty": _clamp(float(explicit.get("uncertainty", 0.42 - 0.25 * det_conf + rng.random() * 0.25))),
            "ammo_need": _clamp(float(explicit.get("ammo_need", 0.25 + 0.55 * threat_score + rng.gauss(0, 0.08)))),
        }
        targets.append(target)
    return targets, {"source_results_present": bool(results), "upstream_summary": base}


def _damage_features(target: dict) -> List[float]:
    return [
        float(target.get("pre_area", 0.5)),
        float(target.get("spectral_delta", 0.0)),
        float(target.get("texture_delta", 0.0)),
        float(target.get("heat_signature", 0.0)),
        float(target.get("crater_density", 0.0)),
        float(target.get("normalized_distance", 0.5)),
        float(target.get("detection_confidence", 0.7)),
        float(target.get("threat_score", 0.5)),
    ]


def _situation_features(target: dict, damage_prob: float) -> List[float]:
    return [
        float(target.get("threat_score", 0.5)),
        float(target.get("velocity_norm", 0.5)),
        1.0 - damage_prob,
        float(target.get("uncertainty", 0.3)),
        float(target.get("ammo_need", 0.5)),
    ]


def _cluster_profiles(targets: List[dict], labels: List[int], probs: List[float]) -> Dict[int, str]:
    scores: Dict[int, List[float]] = {}
    for target, label, prob in zip(targets, labels, probs):
        risk = float(target.get("threat_score", 0.0)) * (1.0 - prob) + float(target.get("velocity_norm", 0.0)) + float(target.get("uncertainty", 0.0))
        scores.setdefault(label, []).append(risk)
    ranked = sorted(scores, key=lambda label: _mean(scores[label]))
    names = ["stable", "watch", "critical"]
    return {label: names[min(idx, len(names) - 1)] for idx, label in enumerate(ranked)}


def _mission_features(targets: List[dict], probs: List[float], control_latency_ms: float) -> List[float]:
    damage_rate = _mean(probs)
    asset_readiness = _clamp(0.92 - 0.18 * _mean([float(t.get("ammo_need", 0.5)) for t in targets]))
    control_timeliness = _clamp(1.0 - control_latency_ms / 1000.0)
    intel_confidence = _mean([float(t.get("detection_confidence", 0.7)) for t in targets])
    threat_pressure = _mean([float(t.get("threat_score", 0.5)) * (1.0 - p) for t, p in zip(targets, probs)])
    ammo_pressure = _mean([float(t.get("ammo_need", 0.5)) for t in targets])
    comm_quality = 0.88
    return [damage_rate, asset_readiness, control_timeliness, intel_confidence, threat_pressure, ammo_pressure, comm_quality]


def _choose_action(target: dict, damage_prob: float, situation: str, mission_completion: float) -> Tuple[str, float]:
    threat_score = float(target.get("threat_score", 0.0))
    uncertainty = float(target.get("uncertainty", 0.0))
    if damage_prob >= 0.84:
        return "confirm_effect_and_shift", 0.02
    if situation == "critical" and threat_score >= 0.72 and damage_prob < 0.72:
        return "re_attack", 0.18
    if uncertainty > 0.34 or damage_prob < 0.55:
        return "reallocate_sensor", 0.08
    if mission_completion < 0.90 and threat_score >= 0.62:
        return "coordinated_suppression", 0.12
    return "continue_tracking", 0.04


def _apply_action(target: dict, action: str, effect_delta: float) -> None:
    if action in {"re_attack", "coordinated_suppression"}:
        target["spectral_delta"] = _clamp(float(target["spectral_delta"]) + effect_delta)
        target["texture_delta"] = _clamp(float(target["texture_delta"]) + effect_delta * 0.75)
        target["heat_signature"] = _clamp(float(target["heat_signature"]) + effect_delta * 0.65)
        target["crater_density"] = _clamp(float(target["crater_density"]) + effect_delta * 0.55)
        target["velocity_norm"] = _clamp(float(target["velocity_norm"]) - effect_delta * 0.70)
        target["uncertainty"] = _clamp(float(target["uncertainty"]) - 0.08)
        target["ammo_need"] = _clamp(float(target["ammo_need"]) + 0.02)
    elif action == "reallocate_sensor":
        target["detection_confidence"] = _clamp(float(target["detection_confidence"]) + 0.12)
        target["uncertainty"] = _clamp(float(target["uncertainty"]) - 0.15)
    elif action == "confirm_effect_and_shift":
        target["threat_score"] = _clamp(float(target["threat_score"]) - 0.18)
        target["uncertainty"] = _clamp(float(target["uncertainty"]) - 0.10)
    else:
        target["uncertainty"] = _clamp(float(target["uncertainty"]) - 0.04)


def _closed_loop_optimization(arguments: dict) -> dict:
    start_time = time.perf_counter()
    seed = int(arguments.get("seed") or 20260412)
    cycles = max(1, min(8, int(arguments.get("cycles") or 3)))
    paths = _dataset_paths(arguments)
    trained = _train_models(seed, paths)
    damage_model: LogisticRegressionGD = trained["damage_model"]
    kmeans: KMeans = trained["kmeans"]
    mission_model: RandomForestRegressor = trained["mission_model"]
    metrics = dict(trained["metrics"])
    targets, source_info = _build_live_targets(arguments, seed)

    history: List[dict] = []
    final_commands: List[dict] = []
    final_assessments: List[dict] = []
    initial_completion = 0.0
    final_completion = 0.0
    update_latencies: List[float] = []

    for cycle in range(1, cycles + 1):
        cycle_start = time.perf_counter()
        damage_rows = [_damage_features(target) for target in targets]
        probs = damage_model.predict_proba(damage_rows)
        situation_rows = [_situation_features(target, prob) for target, prob in zip(targets, probs)]
        cluster_labels = kmeans.predict(situation_rows)
        profiles = _cluster_profiles(targets, cluster_labels, probs)
        mission_features = _mission_features(targets, probs, control_latency_ms=0.0)
        mission_completion = mission_model.predict_one(mission_features)
        if cycle == 1:
            initial_completion = mission_completion
        final_completion = mission_completion

        commands: List[dict] = []
        assessments: List[dict] = []
        for target, prob, label in zip(targets, probs, cluster_labels):
            situation = profiles.get(label, "watch")
            action, effect_delta = _choose_action(target, prob, situation, mission_completion)
            priority = _clamp(float(target.get("threat_score", 0.0)) * (1.0 - prob) + float(target.get("uncertainty", 0.0)))
            commands.append(
                {
                    "target_id": target["target_id"],
                    "action": action,
                    "priority": round(priority, 4),
                    "expected_effect_delta": round(effect_delta, 4),
                    "situation_cluster": situation,
                }
            )
            assessments.append(
                {
                    "target_id": target["target_id"],
                    "damage_probability": round(prob, 4),
                    "damage_confirmed": prob >= 0.5,
                    "situation_cluster": situation,
                    "threat_score": round(float(target.get("threat_score", 0.0)), 4),
                    "uncertainty": round(float(target.get("uncertainty", 0.0)), 4),
                }
            )
            _apply_action(target, action, effect_delta)

        update_latency = time.perf_counter() - cycle_start
        update_latencies.append(update_latency)
        action_counts: Dict[str, int] = {}
        for command in commands:
            action = str(command["action"])
            action_counts[action] = action_counts.get(action, 0) + 1
        history.append(
            {
                "cycle": cycle,
                "mission_completion": round(mission_completion, 4),
                "mean_damage_probability": round(_mean(probs), 4),
                "critical_targets": sum(1 for item in assessments if item["situation_cluster"] == "critical"),
                "action_counts": action_counts,
                "update_latency_seconds": round(update_latency, 6),
            }
        )
        final_commands = sorted(commands, key=lambda item: float(item["priority"]), reverse=True)
        final_assessments = assessments

    total_latency = time.perf_counter() - start_time
    max_update_latency = max(update_latencies) if update_latencies else total_latency
    metrics["situation_update_latency_seconds"] = max_update_latency
    metrics["processed_target_count"] = len(targets)
    metrics["mission_completion_initial"] = initial_completion
    metrics["mission_completion_final"] = final_completion
    metrics["mission_completion_improvement"] = final_completion - initial_completion

    requirement_report = {
        "xbd_damage_accuracy_requirement": 0.92,
        "xbd_damage_accuracy_actual": round(float(metrics["damage_accuracy"]), 4),
        "meets_xbd_damage_accuracy": bool(metrics["damage_accuracy"] >= 0.92),
        "situation_update_frequency_requirement_seconds": 1.0,
        "situation_update_latency_actual_seconds": round(max_update_latency, 6),
        "meets_situation_update_frequency": bool(max_update_latency <= 1.0),
        "target_count_requirement": 50,
        "target_count_actual": len(targets),
        "meets_target_count": bool(len(targets) >= 50),
        "sc2le_task_completion_accuracy_requirement": 0.90,
        "sc2le_task_completion_accuracy_actual": round(float(metrics["task_completion_accuracy"]), 4),
        "meets_sc2le_task_completion_accuracy": bool(metrics["task_completion_accuracy"] >= 0.90),
    }
    meets_all = all(
        bool(requirement_report[key])
        for key in (
            "meets_xbd_damage_accuracy",
            "meets_situation_update_frequency",
            "meets_target_count",
            "meets_sc2le_task_completion_accuracy",
        )
    )

    output = {
        "algorithm": {
            "damage_assessment": "from-scratch logistic regression classifier",
            "situation_assessment": "from-scratch K-Means clustering",
            "mission_evaluation": "from-scratch random forest regression",
            "closed_loop_policy": "rule-constrained receding-horizon control over damage probability, threat, uncertainty and mission completion",
        },
        "datasets": {
            "damage_assessment": trained["data_sources"]["damage_assessment"],
            "mission_evaluation": trained["data_sources"]["mission_evaluation"],
            "expected_xbd_feature_columns": [
                "pre_area",
                "spectral_delta",
                "texture_delta",
                "heat_signature",
                "crater_density",
                "normalized_distance",
                "detection_confidence",
                "threat_score",
                "damage_label",
            ],
            "expected_sc2le_feature_columns": [
                "damage_rate",
                "asset_readiness",
                "control_timeliness",
                "intel_confidence",
                "threat_pressure",
                "ammo_pressure",
                "comm_quality",
                "task_completion",
            ],
        },
        "source_info": source_info,
        "execution_control": {
            "control_cycles": cycles,
            "processed_targets": len(targets),
            "commands": final_commands,
        },
        "effect_assessment": {
            "damage_accuracy": round(float(metrics["damage_accuracy"]), 4),
            "damage_confirmed_count": sum(1 for item in final_assessments if item["damage_confirmed"]),
            "target_assessments": final_assessments,
        },
        "closed_loop_optimization": {
            "mission_completion_initial": round(initial_completion, 4),
            "mission_completion_final": round(final_completion, 4),
            "mission_completion_improvement": round(final_completion - initial_completion, 4),
            "history": history,
        },
        "performance_report": {
            "task_completion_accuracy": round(float(metrics["task_completion_accuracy"]), 4),
            "task_completion_mae": round(float(metrics["task_completion_mae"]), 4),
            "task_completion_r2": round(float(metrics["task_completion_r2"]), 4),
            "max_update_latency_seconds": round(max_update_latency, 6),
            "total_agent_latency_seconds": round(total_latency, 6),
        },
        "requirement_report": requirement_report,
        "meets_requirements": meets_all,
    }
    accuracy = min(float(metrics["damage_accuracy"]), float(metrics["task_completion_accuracy"]))
    if not meets_all:
        accuracy = min(accuracy, 0.89)
    return {
        "task_type": "closed_loop_optimization",
        "input_data": arguments,
        "output_data": output,
        "accuracy": accuracy,
        "latency": total_latency,
    }


TOOLS: Dict[str, Dict[str, Any]] = {
    "closed_loop_optimization": {
        "description": "Execution control, effect assessment and closed-loop optimization using logistic regression, K-Means and random forest regression.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "results": {"type": "object"},
                "targets": {"type": "array", "items": {"type": "object"}},
                "target_count": {"type": "integer"},
                "cycles": {"type": "integer"},
                "seed": {"type": "integer"},
                "dataset_paths": {
                    "type": "object",
                    "properties": {
                        "xbd_damage_csv": {"type": "string"},
                        "sc2le_task_csv": {"type": "string"},
                    },
                },
            },
        },
        "handler": _closed_loop_optimization,
    }
}


def _result_payload(content: dict, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(content, ensure_ascii=False)}],
        "structuredContent": content,
        "isError": is_error,
    }


def _handle_request(message: dict) -> Optional[dict]:
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "closed-loop-agent-mcp-server", "version": "1.0.0"},
            },
        }

    if method == "tools/list":
        tools = []
        for name, definition in TOOLS.items():
            tools.append({"name": name, "description": definition["description"], "inputSchema": definition["inputSchema"]})
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}

    if method == "tools/call":
        params = message.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments", {})
        definition = TOOLS.get(name)
        if not definition:
            return {"jsonrpc": "2.0", "id": request_id, "result": _result_payload({"error": f"Unknown tool: {name}"}, is_error=True)}
        handler: Callable[[dict], dict] = definition["handler"]
        result = handler(arguments if isinstance(arguments, dict) else {})
        return {"jsonrpc": "2.0", "id": request_id, "result": _result_payload(result)}

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            response = _handle_request(message)
            if response is not None:
                sys.stdout.write(json_dumps(response) + "\n")
                sys.stdout.flush()
        except Exception as exc:  # pragma: no cover
            print(f"[closed-loop-agent-mcp-server] {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
