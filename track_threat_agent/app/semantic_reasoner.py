"""Knowledge-graph plus Transformer semantic situation reasoning.

This module is intentionally local and dependency-light. It implements the
algorithmic shape from the project plan with NumPy: metadata and knowledge
relations are converted to semantic tokens, a small self-attention block fuses
the tokens, and the resulting context produces an interpretable semantic factor
and intent probabilities for the downstream DBN threat evaluator.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

import numpy as np

from .models import TrackState
from .utils import clamp


class KGTransformerSemanticReasoner:
    """Local KG+Transformer reasoner for simulated SITREP semantics."""

    TOKEN_DIM = 8

    def reason(self, track: TrackState) -> Dict[str, Any]:
        tokens = self._tokens(track)
        if not tokens:
            tokens = [{"name": "unknown_context", "vector": np.zeros(self.TOKEN_DIM), "risk_prior": 0.18}]

        matrix = np.array([token["vector"] for token in tokens], dtype=float)
        attended, attention = self._self_attention(matrix)
        risk_priors = np.array([float(token["risk_prior"]) for token in tokens], dtype=float)
        attention_mass = attention.mean(axis=0)
        semantic_factor = clamp(0.74 * float(np.dot(attention_mass, risk_priors)) + 0.26 * float(risk_priors.max()))
        intent_probabilities = self._intent_probabilities(track, attended.mean(axis=0), semantic_factor)
        dominant_intent = max(intent_probabilities, key=intent_probabilities.get)

        return {
            "algorithm": "KG+Transformer",
            "semantic_factor": round(semantic_factor, 4),
            "dominant_intent": dominant_intent,
            "intent_probabilities": {key: round(value, 4) for key, value in intent_probabilities.items()},
            "token_count": len(tokens),
            "tokens": [token["name"] for token in tokens],
            "attention": self._compact_attention(tokens, attention),
            "evidence": self._evidence(tokens, semantic_factor, dominant_intent),
            "safety_note": "Semantic reasoning only supports situation-awareness priority; it does not generate engagement advice.",
        }

    def _tokens(self, track: TrackState) -> List[Dict[str, Any]]:
        metadata = track.metadata or {}
        tokens: List[Dict[str, Any]] = []
        for key in ("affiliation", "label", "threat_level", "source_class", "mission_hint", "behavior_hint"):
            value = str(metadata.get(key, "")).strip().lower()
            if value:
                tokens.append(self._token(f"{key}:{value}", self._risk_prior(key, value)))

        for relation in metadata.get("knowledge_relations") or []:
            predicate = str(relation.get("predicate", "")).strip().lower()
            relation_object = str(relation.get("object", "")).strip().lower()
            if predicate or relation_object:
                name = f"kg:{predicate}->{relation_object}"
                tokens.append(self._token(name, self._relation_prior(predicate, relation_object)))

        if track.object_type:
            tokens.append(self._token(f"type:{track.object_type}", self._risk_prior("object_type", track.object_type)))
        return tokens[:12]

    def _token(self, name: str, risk_prior: float) -> Dict[str, Any]:
        vector = np.zeros(self.TOKEN_DIM, dtype=float)
        for index, byte in enumerate(name.encode("utf-8")):
            vector[index % self.TOKEN_DIM] += ((byte % 37) - 18) / 18.0
        norm = np.linalg.norm(vector)
        if norm > 1e-9:
            vector = vector / norm
        vector[0] += risk_prior
        return {"name": name, "vector": vector, "risk_prior": clamp(risk_prior)}

    def _self_attention(self, matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        wq = np.array(
            [
                [0.82, 0.05, -0.02, 0.08, 0.00, 0.04, 0.03, -0.01],
                [0.02, 0.78, 0.06, 0.00, 0.08, -0.04, 0.02, 0.01],
                [0.03, -0.02, 0.80, 0.07, 0.04, 0.01, -0.03, 0.06],
                [0.08, 0.02, 0.04, 0.74, -0.02, 0.05, 0.07, 0.00],
                [0.00, 0.06, 0.05, -0.03, 0.79, 0.07, 0.01, 0.02],
                [0.05, -0.03, 0.02, 0.04, 0.06, 0.76, 0.08, -0.02],
                [0.03, 0.02, -0.04, 0.06, 0.01, 0.08, 0.77, 0.05],
                [-0.01, 0.04, 0.05, 0.01, 0.03, -0.02, 0.06, 0.81],
            ],
            dtype=float,
        )
        wk = wq.T
        wv = np.eye(self.TOKEN_DIM, dtype=float)
        queries = matrix @ wq
        keys = matrix @ wk
        values = matrix @ wv
        scores = queries @ keys.T / math.sqrt(self.TOKEN_DIM)
        scores -= scores.max(axis=1, keepdims=True)
        weights = np.exp(scores)
        attention = weights / np.maximum(weights.sum(axis=1, keepdims=True), 1e-9)
        return attention @ values, attention

    def _intent_probabilities(self, track: TrackState, context: np.ndarray, semantic_factor: float) -> Dict[str, float]:
        anomaly = track.metadata.get("anomaly", {}) or {}
        graph = track.metadata.get("st_gnn_inspired", {}) or {}
        asset_approach = clamp(0.38 * semantic_factor + 0.25 * max(context[0], 0.0) + 0.22 * float(track.speed > 30.0))
        surveillance = clamp(0.30 + 0.34 * semantic_factor + 0.12 * float(track.object_type in {"uav", "aircraft"}))
        formation = clamp(0.20 + 0.55 * float(graph.get("graph_influence", 0.0)) + 0.12 * float(track.object_type in {"aircraft", "ship"}))
        anomalous_maneuver = clamp(
            0.18
            + 0.34 * float(bool(anomaly.get("heading_jump")))
            + 0.26 * float(bool(anomaly.get("speed_jump")))
            + 0.12 * float(bool(anomaly.get("low_confidence")))
        )
        transit = clamp(1.0 - max(asset_approach, surveillance, formation, anomalous_maneuver) * 0.82)
        raw = {
            "asset_approach": asset_approach,
            "surveillance_or_probe": surveillance,
            "formation_coordination": formation,
            "anomalous_maneuver": anomalous_maneuver,
            "transit_or_background": transit,
        }
        total = sum(raw.values()) or 1.0
        return {key: value / total for key, value in raw.items()}

    def _risk_prior(self, key: str, value: str) -> float:
        table = {
            "affiliation:red": 0.95,
            "affiliation:hostile": 0.95,
            "affiliation:unknown": 0.46,
            "affiliation:blue": 0.02,
            "affiliation:friendly": 0.02,
            "label:hostile": 0.95,
            "label:suspicious": 0.68,
            "label:unknown": 0.38,
            "label:friendly": 0.02,
            "threat_level:high": 0.96,
            "threat_level:medium": 0.62,
            "threat_level:low": 0.18,
            "object_type:unknown": 0.62,
            "object_type:uav": 0.58,
            "object_type:aircraft": 0.52,
            "object_type:ship": 0.42,
        }
        return table.get(f"{key}:{value}", 0.32)

    def _relation_prior(self, predicate: str, relation_object: str) -> float:
        text = f"{predicate} {relation_object}"
        if "protected" in text or "asset" in text or "command" in text:
            return 0.82
        if "formation" in text or "cooperate" in text or "screen" in text:
            return 0.66
        if "sensor" in text or "recon" in text or "surveillance" in text:
            return 0.58
        return 0.35

    def _compact_attention(self, tokens: List[Dict[str, Any]], attention: np.ndarray) -> List[Dict[str, float | str]]:
        if attention.size == 0:
            return []
        mass = attention.mean(axis=0)
        ranked = sorted(enumerate(mass), key=lambda item: item[1], reverse=True)[:5]
        return [
            {"token": tokens[index]["name"], "attention_weight": round(float(weight), 4)}
            for index, weight in ranked
        ]

    def _evidence(self, tokens: List[Dict[str, Any]], semantic_factor: float, dominant_intent: str) -> List[str]:
        top_tokens = sorted(tokens, key=lambda token: token["risk_prior"], reverse=True)[:3]
        token_text = ", ".join(token["name"] for token in top_tokens) if top_tokens else "unknown_context"
        return [
            f"KG tokens considered: {token_text}",
            f"Transformer self-attention semantic factor is {semantic_factor:.2f}",
            f"dominant situation intent class is {dominant_intent}",
        ]
