"""EDL 门控工具（无 torch），供算法包 mock/真推理共用判定逻辑。"""

from __future__ import annotations

from typing import Any, Literal

Decision = Literal["verified", "rejected", "manual_review"]


def detector_confidence(det: dict[str, Any]) -> float:
    for key in ("rtdetr_confidence", "detector_confidence", "confidence"):
        if det.get(key) is not None:
            try:
                return float(det[key])
            except (TypeError, ValueError):
                continue
    return 0.5


def with_base_fields(det: dict[str, Any], *, detector_conf: float) -> dict[str, Any]:
    out = dict(det)
    out.setdefault("rtdetr_confidence", detector_conf)
    out["detector_confidence"] = round(detector_conf, 4)
    bbox = out.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        out["bbox"] = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
    return out


def decide_from_scores(
    *,
    belief: float,
    epistemic: float,
    min_conf: float,
    max_epistemic: float,
) -> Decision:
    if belief >= min_conf and epistemic <= max_epistemic:
        return "verified"
    if belief >= max(0.25, min_conf * 0.65) or epistemic <= max_epistemic * 1.35:
        return "manual_review"
    return "rejected"
