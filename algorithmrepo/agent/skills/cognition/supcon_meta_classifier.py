"""SupCon + Meta-Learning：监督对比小样本分类。"""

from __future__ import annotations

from typing import Any

from agent.skills.base import AlgorithmBackend

_LABELS = ("friendly", "neutral", "hostile", "unknown")


class SupConMetaClassifier(AlgorithmBackend[list[dict[str, Any]]]):
    name = "SupCon+Meta-Learning"

    def run(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        fused = inputs.get("fused_embeddings", {})
        support = inputs.get("support_shots", [])
        if self.use_mock:
            return self._mock_classify(fused)
        return self._infer(fused, support)

    def _mock_classify(self, fused: dict[str, list[float]]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for tid, vec in fused.items():
            score = sum(vec[:8]) / max(len(vec[:8]), 1)
            idx = int(abs(score * 10)) % len(_LABELS)
            label = _LABELS[idx]
            results.append(
                {
                    "target_id": tid,
                    "label": label,
                    "confidence": min(0.99, 0.55 + abs(score) * 0.1),
                    "support_shots": 5,
                }
            )
        return results

    def _infer(
        self, fused: dict[str, list[float]], support: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        from agent.inference.classify import classify_targets

        return classify_targets(fused, {**self.config, "support_shots": support})
