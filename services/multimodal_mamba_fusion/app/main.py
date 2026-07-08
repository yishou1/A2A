#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(ROOT))

from a2a_algorithms_common.http_service import create_algorithm_app
from a2a_algorithms_common.tia_predictors import predict_multimodal_mamba_fusion, tia_model_loaded

ALGORITHM_ID = "multimodal_mamba_fusion"
VERSION = "1.0.0"
PORT = int(os.environ.get("PORT", "9026"))


def _predict(inputs: dict, params: dict) -> dict:
    return predict_multimodal_mamba_fusion(inputs, params)


app = create_algorithm_app(
    ALGORITHM_ID,
    VERSION,
    "fusion",
    _predict,
    model_loaded_callable=lambda: tia_model_loaded(ALGORITHM_ID),
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
