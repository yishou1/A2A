#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(ROOT))

# This override belongs only to the detector process, not the other TIA services.
if "RTDETR_USE_MOCK" in os.environ:
    os.environ["TIA_USE_MOCK"] = os.environ["RTDETR_USE_MOCK"]
if os.environ.get("RTDETR_COMPUTE_PROFILE"):
    os.environ["TIA_COMPUTE_PROFILE"] = os.environ["RTDETR_COMPUTE_PROFILE"]

from a2a_algorithms_common.http_service import create_algorithm_app
from a2a_algorithms_common.tia_predictors import predict_battlefield_rtdetr_detector, tia_model_loaded, tia_execution_mode

ALGORITHM_ID = "battlefield_rtdetr_detector"
VERSION = "1.0.0"
PORT = int(os.environ.get("PORT", "9020"))
INFERENCE_MODE = "mock" if os.environ.get("TIA_USE_MOCK", "1") not in ("0", "false", "False") else "real"
SERVICE_METADATA = {"inference_mode": INFERENCE_MODE}


def _predict(inputs: dict, params: dict) -> dict:
    # Native algorithm cards and older AMOS callers use the host's loopback URL.
    # Inside Docker only rewrite released AMOS static-resource paths to its service name.
    if os.environ.get("A2A_CONTAINER_MODE") == "1":
        frames = []
        for frame in inputs.get("frames") or []:
            payload = dict(frame.get("payload") or {})
            uri = payload.get("image_uri")
            if isinstance(uri, str):
                parsed = urlsplit(uri)
                if (parsed.hostname in ("localhost", "127.0.0.1") and parsed.port == 5000
                        and parsed.path.startswith("/static/")):
                    base = os.environ.get("AMOS_PUBLIC_BASE_URL", "http://amos:5000/")
                    payload["image_uri"] = urljoin(base.rstrip("/") + "/", parsed.path.lstrip("/"))
            frames.append({**frame, "payload": payload})
        inputs = {**inputs, "frames": frames}
    return predict_battlefield_rtdetr_detector(inputs, params)


app = create_algorithm_app(
    ALGORITHM_ID,
    VERSION,
    "detection",
    _predict,
    model_loaded_callable=lambda: tia_model_loaded(ALGORITHM_ID),
    inference_mode_callable=tia_execution_mode,
    extra_metadata=SERVICE_METADATA,
)

if __name__ == "__main__":
    import uvicorn

    if INFERENCE_MODE == "real":
        from a2a_algorithms_common.tia_predictors import _backend_config
        from agent.inference.registry import get_detector, detector_runtime_profile

        config = _backend_config("agent.skills.perception.rt_detr_odconv_detector")
        detector = get_detector(config)
        expected_classes = int(os.environ.get("RTDETR_EXPECTED_CLASSES", "0"))
        if expected_classes and len(detector.names) != expected_classes:
            raise RuntimeError(f"Expected {expected_classes} detector classes, got {len(detector.names)}")
        SERVICE_METADATA.update(detector_runtime_profile(config))
        SERVICE_METADATA["num_classes"] = len(detector.names)

    default_host = "0.0.0.0" if os.environ.get("A2A_CONTAINER_MODE") == "1" else "127.0.0.1"
    uvicorn.run(app, host=os.environ.get("RTDETR_BIND_HOST", default_host), port=PORT)
