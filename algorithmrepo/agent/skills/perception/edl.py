"""EDL（Evidential Deep Learning）：检测验证与不确定性估计。"""

from __future__ import annotations

from typing import Any

from agent.skills.base import AlgorithmBackend


class EDLEvidentialVerifier(AlgorithmBackend[list[dict[str, Any]]]):
    name = "EDL-Evidential-Deep-Learning"

    def run(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = inputs.get("detections", [])
        return_all = bool(inputs.get("return_all_assessments", False))
        if self.use_mock:
            assessments = self._mock_assess(candidates)
            return assessments if return_all else [item for item in assessments if item["verified"]]
        return self._infer(candidates, return_all=return_all)

    def _mock_assess(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        assessments: list[dict[str, Any]] = []
        for det in candidates:
            conf = float(det.get("confidence", 0.5))
            epistemic = max(0.0, 1.0 - conf)
            is_verified = conf >= 0.6 and epistemic < 0.35
            assessments.append(
                {
                    **det,
                    "verified": is_verified,
                    "decision": "verified" if is_verified else "rejected",
                    "review_required": False,
                    "epistemic_uncertainty": round(epistemic, 4),
                    "aleatoric_uncertainty": round(epistemic * 0.6, 4),
                }
            )
        return assessments

    def _infer(
        self, candidates: list[dict[str, Any]], *, return_all: bool
    ) -> list[dict[str, Any]]:
        from agent.inference.edl import assess_detections, verify_detections

        return (
            assess_detections(candidates, self.config)
            if return_all
            else verify_detections(candidates, self.config)
        )
