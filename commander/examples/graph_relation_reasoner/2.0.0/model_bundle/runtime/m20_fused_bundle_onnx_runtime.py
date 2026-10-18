"""Standalone ONNX Runtime facade for an exported M20 four-expert bundle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unicodedata

import numpy as np
import onnxruntime as ort


DOMAINS = ("aircraft", "uav", "ship", "ground_vehicle")
TYPE_NAME_DOMAINS = ("aircraft", "uav", "ship", "ground_vehicle", "static", "unknown")
FEATURE_DIMENSION = 512


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
    for token_value in _tokens(value):
        bucket = int.from_bytes(hashlib.sha256(token_value.encode("utf-8")).digest()[:4], "little") % FEATURE_DIMENSION
        encoded[bucket] += 1.0
    norm = float(np.linalg.norm(encoded))
    return encoded / norm if norm else encoded


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits, axis=-1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=-1, keepdims=True)


class M20FusedOnnxBundle:
    """Route from ``type_name`` and execute a selected ONNX graph expert."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if self.manifest.get("schema_version") != "m20_fused_expert_onnx_bundle/v1":
            raise ValueError("unsupported M20 ONNX bundle schema")
        type_manifest = json.loads((root / "type_name_router/manifest.json").read_text(encoding="utf-8"))
        if tuple(type_manifest["outputs"]) != TYPE_NAME_DOMAINS:
            raise ValueError("unsupported type-name router output schema")
        self.minimum_confidence = float(type_manifest["minimum_confidence"])
        self.minimum_support = float(type_manifest["minimum_ngram_support"])
        self.known_ngrams = frozenset(type_manifest["known_ngram_tokens"])
        providers = ["CPUExecutionProvider"]
        self.type_router = ort.InferenceSession(str(root / "type_name_router/model.onnx"), providers=providers)
        self.experts = {
            domain: ort.InferenceSession(str(root / "experts" / domain / "model.onnx"), providers=providers)
            for domain in DOMAINS
        }

    def route(self, *, type_name: str | None) -> dict[str, object]:
        """Return an expert selection, or explicitly decline an unknown name."""
        if not type_name or not type_name.strip():
            return {"domain": None, "method": "trained_type_name_classifier_rejected", "confidence": 0.0, "fallback_reason": "missing_type_name"}
        tokens = _tokens(type_name)
        support = sum(token in self.known_ngrams for token in tokens) / max(len(tokens), 1)
        if support < self.minimum_support:
            return {"domain": None, "method": "trained_type_name_classifier_rejected", "confidence": 0.0, "fallback_reason": "unseen_type_name_vocabulary"}
        logits = self.type_router.run(None, {"type_name_features": _features(type_name)[None]})[0]
        probability = _softmax(logits)[0]
        index = int(probability.argmax())
        confidence = float(probability[index])
        if confidence < self.minimum_confidence:
            return {"domain": None, "method": "trained_type_name_classifier_rejected", "confidence": confidence, "fallback_reason": "low_type_name_classifier_confidence"}
        domain = TYPE_NAME_DOMAINS[index]
        if domain == "static":
            return {"domain": None, "method": "trained_type_name_classifier_rejected", "confidence": confidence, "fallback_reason": "static_or_non_group_type_name"}
        if domain == "unknown":
            return {"domain": None, "method": "trained_type_name_classifier_rejected", "confidence": confidence, "fallback_reason": "unknown_type_name_classifier"}
        return {"domain": domain, "method": "trained_type_name_classifier", "confidence": confidence, "fallback_reason": None}

    def score_graph(
        self,
        domain: str,
        node_sequences: np.ndarray,
        node_time_mask: np.ndarray,
        edge_inputs: np.ndarray,
        node_mask: np.ndarray,
    ) -> np.ndarray:
        """Return symmetric pair probabilities from one standard M20 graph."""
        if domain not in self.experts:
            raise ValueError(f"unsupported expert domain: {domain}")
        inputs = {
            "node_sequences": np.asarray(node_sequences, dtype=np.float32),
            "node_time_mask": np.asarray(node_time_mask, dtype=bool),
            "edge_inputs": np.asarray(edge_inputs, dtype=np.float32),
            "node_mask": np.asarray(node_mask, dtype=bool),
        }
        logits = self.experts[domain].run(["pair_logits"], inputs)[0]
        return 1.0 / (1.0 + np.exp(-logits))
