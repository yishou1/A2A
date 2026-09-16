"""edl_evidential_verifier — 独立算法入口。"""

from __future__ import annotations

from typing import Any

from algorithms.edl_evidential_verifier.backend import EDLEvidentialVerifier

ALGORITHM_ID = "edl_evidential_verifier"
CONFIG_KEY = "edl"


def predict(
    inputs: dict[str, Any],
    *,
    use_mock: bool = True,
    config: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """独立推理。inputs 需含 detections。

    返回完整门控报告；下游感知链仍主要消费 verified_detections。
    """
    cfg = dict(config or {})
    if params:
        cfg.update(params)
    return EDLEvidentialVerifier(use_mock=use_mock, config=cfg).assess(
        {"detections": inputs.get("detections") or []}
    )


__all__ = ["ALGORITHM_ID", "CONFIG_KEY", "EDLEvidentialVerifier", "predict"]
