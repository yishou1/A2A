#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.http_service import create_algorithm_app
from a2a_algorithms_common.service_predictors import predict_closed_loop_decision_advisor, mission_model_loaded

ALGORITHM_ID = "closed_loop_decision_advisor"
VERSION = "1.0.0"
PORT = int(os.environ.get("PORT", "9015"))


def _predict(inputs: dict, params: dict) -> dict:
    return predict_closed_loop_decision_advisor(inputs, params)


_model_loaded = mission_model_loaded if "closed_loop_decision_advisor" == "mission_completion_scorer" else (lambda: True)

app = create_algorithm_app(
    ALGORITHM_ID,
    VERSION,
    "decision",
    _predict,
    model_loaded_callable=_model_loaded,
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
