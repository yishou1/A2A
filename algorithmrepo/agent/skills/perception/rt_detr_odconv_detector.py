"""RT-DETR + ODConv：实时目标检测。"""

from __future__ import annotations

from typing import Any

from agent.skills.base import AlgorithmBackend


class RTDETRODConvDetector(AlgorithmBackend[list[dict[str, Any]]]):
    name = "RT-DETR+ODConv"

    def run(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        frames = inputs.get("frames", [])
        if self.use_mock:
            return self._mock_detect(frames)
        return self._infer(frames)

    def _mock_detect(self, frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i, frame in enumerate(frames):
            if frame.get("modality") not in ("eo_ir", "sar"):
                continue
            out.append(
                {
                    "sensor_id": frame.get("sensor_id", f"s{i}"),
                    "class_name": "vehicle",
                    "confidence": 0.89,
                    "bbox": [120.0, 80.0, 280.0, 220.0],
                    "odconv_refined": True,
                }
            )
        return out

    def _infer(self, frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from agent.inference.vision import detect_objects

        return detect_objects(frames, self.config)
