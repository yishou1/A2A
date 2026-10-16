"""EDL（Evidential Deep Learning）：检测验证与不确定性估计。

mock 路径不依赖 agent/torch；真推理延迟导入 agent.inference.edl。
"""

from __future__ import annotations

from typing import Any

from algorithms._edl_gate import decide_from_scores, detector_confidence, with_base_fields
from algorithms.base import AlgorithmBackend


def summarize_assessments(assessments: list[dict[str, Any]]) -> dict[str, Any]:
    verified = [a for a in assessments if a.get("decision") == "verified"]
    rejected = [a for a in assessments if a.get("decision") == "rejected"]
    review = [a for a in assessments if a.get("decision") == "manual_review"]
    return {
        "assessments": assessments,
        "verified_detections": verified,
        "rejected_detections": rejected,
        "review_queue": review,
        "count": len(verified),
        "summary": {
            "total": len(assessments),
            "verified": len(verified),
            "rejected": len(rejected),
            "manual_review": len(review),
        },
    }


class EDLEvidentialVerifier(AlgorithmBackend[list[dict[str, Any]]]):
    name = "EDL-Evidential-Deep-Learning"
    algorithm_id = "edl_evidential_verifier"
    config_key = "edl"

    def run(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        """兼容旧调用：只返回 verified 列表。"""
        report = self.assess(inputs)
        return list(report.get("verified_detections") or [])

    def assess(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """完整评估报告（供 predict / 联调展示）。"""
        candidates = list(inputs.get("detections") or [])
        if self.use_mock:
            assessments = self._mock_assess(candidates)
        else:
            assessments = self._infer_assess(candidates)
        return summarize_assessments(assessments)

    def _mock_assess(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # 与 agent.inference.edl._thresholds 默认一致
        min_conf = float(self.config.get("edl_min_confidence", 0.35))
        max_epistemic = float(self.config.get("edl_max_epistemic", 0.45))
        out: list[dict[str, Any]] = []
        for det in candidates:
            det_conf = detector_confidence(det)
            epistemic = max(0.0, 1.0 - det_conf)
            aleatoric = round(epistemic * 0.6, 4)
            decision = decide_from_scores(
                belief=det_conf,
                epistemic=epistemic,
                min_conf=min_conf,
                max_epistemic=max_epistemic,
            )
            item = with_base_fields(det, detector_conf=det_conf)
            item.update(
                {
                    "verified": decision == "verified",
                    "decision": decision,
                    "review_required": decision == "manual_review",
                    "confidence": round(det_conf, 4),
                    "edl_belief": round(det_conf, 4),
                    "epistemic_uncertainty": round(epistemic, 4),
                    "aleatoric_uncertainty": aleatoric,
                }
            )
            out.append(item)
        return out

    def _infer_assess(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from agent.inference.edl import assess_detections

        return assess_detections(candidates, self.config)
