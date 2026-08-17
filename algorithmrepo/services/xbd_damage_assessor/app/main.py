#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.http_service import create_algorithm_app
from a2a_algorithms_common.service_predictors import predict_xbd_damage_assessor, xbd_damage_model_loaded

ALGORITHM_ID = "xbd_damage_assessor"
VERSION = "1.0.0"
PORT = int(os.environ.get("PORT", "9016"))


def _predict(inputs: dict, params: dict) -> dict:
    return predict_xbd_damage_assessor(inputs, params)


app = create_algorithm_app(
    ALGORITHM_ID,
    VERSION,
    "scoring",
    _predict,
    model_loaded_callable=xbd_damage_model_loaded,
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
