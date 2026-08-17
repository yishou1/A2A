"""EDL（Evidential Deep Learning）：检测验证与不确定性估计。"""

from __future__ import annotations

from typing import Any

from agent.skills.base import AlgorithmBackend


class EDLEvidentialVerifier(AlgorithmBackend[list[dict[str, Any]]]):
    name = "EDL-Evidential-Deep-Learning"

    def run(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = inputs.get("detections", [])
        if self.use_mock:
            return self._mock_verify(candidates)
        return self._infer(candidates)

    def _mock_verify(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        verified: list[dict[str, Any]] = []
        for det in candidates:
            conf = float(det.get("confidence", 0.5))
            epistemic = max(0.0, 1.0 - conf)
            if conf >= 0.6 and epistemic < 0.35:
                verified.append(
                    {
                        **det,
                        "verified": True,
                        "epistemic_uncertainty": round(epistemic, 4),
                        "aleatoric_uncertainty": round(epistemic * 0.6, 4),
                    }
                )
        return verified

    def _infer(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from agent.inference.edl import verify_detections

        return verify_detections(candidates, self.config)
