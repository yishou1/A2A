"""Pickle compatibility shims for models trained in closed_loop_agent.closed_loop_core."""
from __future__ import annotations

import math
import random
import sys
import types
from typing import List, Optional, Sequence, Tuple


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


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


class RegressionTree:
    def __init__(
        self,
        max_depth: int = 6,
        min_leaf: int = 8,
        max_features: Optional[int] = None,
        seed: int = 11,
    ) -> None:
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


def register_pickle_aliases() -> None:
    """Register sys.modules aliases so pickled models from A2A closed_loop_agent can load."""
    closed_loop_pkg = sys.modules.get("closed_loop_agent")
    if closed_loop_pkg is None:
        closed_loop_pkg = types.ModuleType("closed_loop_agent")
        closed_loop_pkg.__path__ = []  # type: ignore[attr-defined]
        sys.modules["closed_loop_agent"] = closed_loop_pkg

    core_mod = types.ModuleType("closed_loop_agent.closed_loop_core")
    core_mod.StandardScaler = StandardScaler
    core_mod.RegressionTree = RegressionTree
    core_mod.RandomForestRegressor = RandomForestRegressor
    sys.modules["closed_loop_agent.closed_loop_core"] = core_mod
    closed_loop_pkg.closed_loop_core = core_mod  # type: ignore[attr-defined]
