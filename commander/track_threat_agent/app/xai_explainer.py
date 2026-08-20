"""Explainability helpers for situation-awareness priority outputs."""

from __future__ import annotations

from typing import Dict, List

from .models import TrackState


class XAIExplanationBuilder:
    """Build Chinese evidence chains and auditable model traces."""

    SAFETY_NOTE = "仅表示态势关注优先级，不包含武器控制、目标选择、制导、交战或打击建议"
    PATTERN_LABELS = {
        "protected_zone_approach": "接近保护区域",
        "sustained_presence": "持续稳定出现",
        "coordinated_motion": "协同运动",
        "anomalous_motion": "异常运动",
        "non_closing_motion": "未持续接近",
    }

    def threat_metadata(
        self,
        track: TrackState,
        factors: Dict[str, float],
        weighted_score: float,
        dbn_result: Dict[str, object],
    ) -> Dict[str, object]:
        contributions = {
            "distance": round(0.28 * factors.get("distance_factor", 0.0), 4),
            "closing": round(0.24 * factors.get("closing_factor", 0.0), 4),
            "type": round(0.18 * factors.get("type_factor", 0.0), 4),
            "anomaly": round(0.18 * factors.get("anomaly_factor", 0.0), 4),
            "quality": round(0.12 * factors.get("quality_factor", 0.0), 4),
            "dbn_state": round(0.24 * float(dbn_result.get("state_factor", 0.0)), 4),
        }
        dbn_transition = dbn_result.get("state_transition", {}) or {}
        pattern_transition = dbn_result.get("risk_pattern_transition", {}) or {}
        factor_chain = [
            {"factor": key, "contribution": value}
            for key, value in sorted(contributions.items(), key=lambda item: item[1], reverse=True)
        ]
        return {
            "xai": {
                "algorithm": "XAI",
                "contract": "situation_attention_explainable_evidence_chain",
                "language": "zh-CN",
                "evidence_chain": self.threat_evidence_chain(track, factors, weighted_score, dbn_result),
                "factor_contributions": contributions,
                "factor_chain": factor_chain,
                "dbn_transition_evidence": {
                    "observation_reliability": dbn_result.get("observation_reliability"),
                    "posterior_entropy": dbn_result.get("posterior_entropy"),
                    "high_delta": dbn_transition.get("high_delta"),
                    "dominant_observable_pattern": dbn_result.get("dominant_risk_pattern"),
                    "observable_pattern_changed": pattern_transition.get("dominant_changed"),
                    "parameter_model": dbn_result.get("parameter_model"),
                },
                "safety_chain": [
                    "该结果仅表示仿真或接入态势中的关注优先级",
                    "模式概率只描述可观测的运动与接近状态",
                    "不包含武器控制、目标选择、制导、交战或打击建议",
                ],
                "model_trace": self._model_trace(track, dbn_result),
                "safety_note": self.SAFETY_NOTE,
            }
        }

    def threat_evidence_chain(
        self,
        track: TrackState,
        factors: Dict[str, float],
        weighted_score: float,
        dbn_result: Dict[str, object],
    ) -> List[str]:
        posterior = dbn_result.get("posterior", {}) or {}
        state_transition = dbn_result.get("state_transition", {}) or {}
        pattern = str(dbn_result.get("dominant_risk_pattern", "unknown"))
        parameter_model = dbn_result.get("parameter_model", {}) or {}
        prediction = track.metadata.get("prediction", {}) or {}
        return [
            f"航迹 {track.track_id} 类型为 {track.object_type}，航迹质量 {track.track_quality:.2f}",
            (
                f"距离因子 {factors.get('distance_factor', 0.0):.2f}，"
                f"接近因子 {factors.get('closing_factor', 0.0):.2f}，"
                f"异常因子 {factors.get('anomaly_factor', 0.0):.2f}"
            ),
            f"航线预测模型为 {prediction.get('model', 'adaptive_multi_model_physics')}",
            f"DBN 平滑前加权分数为 {weighted_score:.2f}",
            f"DBN 观测可信度为 {float(dbn_result.get('observation_reliability', 0.0)):.2f}",
            f"DBN 高关注状态概率变化量为 {float(state_transition.get('high_delta', 0.0)):.2f}",
            (
                "DBN 后验低/中/高概率为 "
                f"{posterior.get('low', 0.0):.2f}/"
                f"{posterior.get('medium', 0.0):.2f}/"
                f"{posterior.get('high', 0.0):.2f}"
            ),
            f"当前主导可观测模式为 {self.PATTERN_LABELS.get(pattern, pattern)}",
            (
                f"DBN 参数版本 {parameter_model.get('model_version', 'unknown')}，"
                f"校验哈希 {str(parameter_model.get('sha256', 'unknown'))[:12]}"
            ),
            self.SAFETY_NOTE,
        ]

    @staticmethod
    def _model_trace(track: TrackState, dbn_result: Dict[str, object]) -> List[str]:
        prediction = track.metadata.get("prediction", {}) or {}
        trace = [
            "全局最近邻关联与 Kalman/alpha-beta 航迹更新",
            f"{prediction.get('model', 'adaptive_multi_model_physics')} 航线预测",
        ]
        st_gnn_runtime = track.metadata.get("st_gnn_runtime", {}) or {}
        if st_gnn_runtime.get("applied"):
            trace.append(f"ST-GNN TorchScript 残差修正 {st_gnn_runtime.get('model_version', '')}".strip())
        else:
            trace.append("ST-GNN 未启用，本帧使用物理模型回退")
        trace.extend(
            [
                "多因子态势关注基础评分",
                f"DBN 三状态后验平滑 {((dbn_result.get('parameter_model') or {}).get('model_version', 'unknown'))}",
                "可观测运动模式概率校准",
            ]
        )
        return trace
