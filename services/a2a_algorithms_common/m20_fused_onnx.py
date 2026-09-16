"""ONNX Runtime adapter for the M20 multi-platform temporal relation bundle.

The public algorithm interface stays track-oriented.  This module owns the
type-name routing and conversion from track history to the fixed ONNX tensor
contract, so callers do not need to know about individual expert models.
"""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from pathlib import Path
from typing import Any, Sequence

import numpy as np

try:
    import onnxruntime as ort
except ImportError:  # pragma: no cover - reflected by the caller as unavailable
    ort = None  # type: ignore[assignment]


DOMAINS = ("aircraft", "uav", "ship", "ground_vehicle")
ROUTER_DOMAINS = (*DOMAINS, "static", "unknown")
FEATURE_DIMENSION = 512
HISTORY_POINTS = 8
MAX_NODES = 10


def _tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    normalized = " ".join(normalized.replace("_", " ").replace("-", " ").split())
    normalized = f"^{normalized}$"
    return tuple(
        normalized[start : start + width]
        for width in range(1, 6)
        for start in range(max(0, len(normalized) - width + 1))
    )


def _features(value: str) -> np.ndarray:
    encoded = np.zeros(FEATURE_DIMENSION, dtype=np.float32)
    for token in _tokens(value):
        bucket = int.from_bytes(
            hashlib.sha256(token.encode("utf-8")).digest()[:4], "little"
        ) % FEATURE_DIMENSION
        encoded[bucket] += 1.0
    norm = float(np.linalg.norm(encoded))
    return encoded / norm if norm else encoded


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits, axis=-1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=-1, keepdims=True)


def track_type_name(track: dict[str, Any]) -> str | None:
    """Read the upstream taxonomy without forcing a particular producer field."""
    metadata = track.get("metadata") if isinstance(track.get("metadata"), dict) else {}
    for value in (
        track.get("type_name"),
        metadata.get("type_name"),
        track.get("class_name"),
        metadata.get("source_class"),
        track.get("platform_type"),
        metadata.get("platform_type"),
        track.get("object_type"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _point(track: dict[str, Any], item: dict[str, Any]) -> dict[str, float]:
    return {
        "lat": _number(item.get("lat"), _number(track.get("lat"))),
        "lon": _number(item.get("lon"), _number(track.get("lon"))),
        "alt": _number(item.get("alt"), _number(track.get("alt"))),
        "speed": _number(item.get("speed"), _number(track.get("speed"))),
        "heading": _number(item.get("heading"), _number(track.get("heading"))),
        "confidence": min(1.0, max(0.0, _number(item.get("confidence"), _number(track.get("confidence"), 0.75)))),
    }


def _history(track: dict[str, Any]) -> list[dict[str, float]]:
    raw = track.get("history_path")
    points = [_point(track, item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    current = _point(track, track)
    if not points or points[-1]["lat"] != current["lat"] or points[-1]["lon"] != current["lon"]:
        points.append(current)
    return points[-HISTORY_POINTS:]


class M20FusedOnnxBundle:
    """Routes a type name to an expert and scores a standard temporal graph."""

    def __init__(self, root: Path) -> None:
        if ort is None:
            raise RuntimeError("onnxruntime is required for M20 fused ONNX inference")
        self.root = root
        self.manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if self.manifest.get("schema_version") != "m20_fused_expert_onnx_bundle/v1":
            raise ValueError("unsupported M20 fused ONNX bundle schema")
        router_manifest = json.loads((root / "type_name_router" / "manifest.json").read_text(encoding="utf-8"))
        if tuple(router_manifest.get("outputs", ())) != ROUTER_DOMAINS:
            raise ValueError("unsupported M20 type-name router output schema")
        self.minimum_confidence = float(router_manifest["minimum_confidence"])
        self.minimum_support = float(router_manifest["minimum_ngram_support"])
        self.known_ngrams = frozenset(router_manifest["known_ngram_tokens"])
        providers = ["CPUExecutionProvider"]
        self.router = ort.InferenceSession(str(root / "type_name_router" / "model.onnx"), providers=providers)
        self.experts = {
            domain: ort.InferenceSession(str(root / "experts" / domain / "model.onnx"), providers=providers)
            for domain in DOMAINS
        }

    def route(self, type_name: str | None) -> dict[str, Any]:
        if not type_name:
            return {"domain": None, "confidence": 0.0, "reason": "missing_type_name"}
        tokens = _tokens(type_name)
        support = sum(token in self.known_ngrams for token in tokens) / max(1, len(tokens))
        if support < self.minimum_support:
            return {"domain": None, "confidence": 0.0, "reason": "unseen_type_name_vocabulary"}
        logits = self.router.run(None, {"type_name_features": _features(type_name)[None]})[0]
        probabilities = _softmax(logits)[0]
        index = int(probabilities.argmax())
        confidence = float(probabilities[index])
        domain = ROUTER_DOMAINS[index]
        if confidence < self.minimum_confidence:
            return {"domain": None, "confidence": confidence, "reason": "low_type_name_classifier_confidence"}
        if domain not in DOMAINS:
            return {"domain": None, "confidence": confidence, "reason": f"{domain}_type_name"}
        return {"domain": domain, "confidence": confidence, "reason": None}

    def score(self, domain: str, tracks: Sequence[dict[str, Any]]) -> tuple[np.ndarray, list[dict[str, Any]]]:
        selected = list(tracks)[:MAX_NODES]
        histories = [_history(track) for track in selected]
        sequences = np.zeros((1, HISTORY_POINTS, MAX_NODES, 7), dtype=np.float32)
        time_mask = np.zeros((1, HISTORY_POINTS, MAX_NODES), dtype=np.bool_)
        node_mask = np.zeros((1, MAX_NODES), dtype=np.bool_)
        node_mask[0, : len(selected)] = True
        aligned: list[list[dict[str, float] | None]] = []
        for history in histories:
            padded: list[dict[str, float] | None] = [None] * (HISTORY_POINTS - len(history)) + history
            aligned.append(padded)
        for step in range(HISTORY_POINTS):
            present = [points[step] for points in aligned if points[step] is not None]
            if not present:
                continue
            mean_lat = sum(point["lat"] for point in present) / len(present)
            mean_lon = sum(point["lon"] for point in present) / len(present)
            east_scale = 111_320.0 * max(math.cos(math.radians(mean_lat)), 0.1)
            for index, points in enumerate(aligned):
                point = points[step]
                if point is None:
                    continue
                heading = math.radians(point["heading"])
                sequences[0, step, index] = (
                    (point["lon"] - mean_lon) * east_scale / 20_000.0,
                    (point["lat"] - mean_lat) * 111_320.0 / 20_000.0,
                    min(1.2, max(0.0, point["speed"] / 350.0)),
                    math.sin(heading), math.cos(heading),
                    min(1.2, max(0.0, point["alt"] / 10_000.0)), point["confidence"],
                )
                time_mask[0, step, index] = True
        edges = np.zeros((1, MAX_NODES, MAX_NODES, 12), dtype=np.float32)
        for left in range(len(selected)):
            for right in range(len(selected)):
                overlap = [step for step in range(HISTORY_POINTS) if aligned[left][step] is not None and aligned[right][step] is not None]
                if not overlap:
                    continue
                distances, headings, speeds = [], [], []
                for step in overlap:
                    first, second = aligned[left][step], aligned[right][step]
                    assert first is not None and second is not None
                    mean_lat = (first["lat"] + second["lat"]) / 2.0
                    east_scale = 111_320.0 * max(math.cos(math.radians(mean_lat)), 0.1)
                    dx = (second["lon"] - first["lon"]) * east_scale / 20_000.0
                    dy = (second["lat"] - first["lat"]) * 111_320.0 / 20_000.0
                    distances.append(math.hypot(dx, dy))
                    headings.append(math.cos(math.radians((first["heading"] - second["heading"] + 180.0) % 360.0 - 180.0)))
                    speeds.append(abs(first["speed"] - second["speed"]) / 350.0)
                first, second = aligned[left][overlap[-1]], aligned[right][overlap[-1]]
                assert first is not None and second is not None
                mean_lat = (first["lat"] + second["lat"]) / 2.0
                east_scale = 111_320.0 * max(math.cos(math.radians(mean_lat)), 0.1)
                dx = (second["lon"] - first["lon"]) * east_scale / 20_000.0
                dy = (second["lat"] - first["lat"]) * 111_320.0 / 20_000.0
                trend = float(np.polyfit(np.arange(len(distances)), distances, 1)[0]) if len(distances) > 1 else 0.0
                edges[0, left, right] = (dx, dy, math.hypot(dx, dy), headings[-1], speeds[-1], 1.0, len(overlap) / HISTORY_POINTS, float(np.mean(distances)), float(np.std(distances)), trend, float(np.mean(headings)), float(np.std(speeds)))
        logits = self.experts[domain].run(["pair_logits"], {
            "node_sequences": sequences,
            "node_time_mask": time_mask,
            "edge_inputs": edges,
            "node_mask": node_mask,
        })[0]
        return 1.0 / (1.0 + np.exp(-logits)), selected
