#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.decision_agent_predictors import (
    predict_compliance_authorization_core,
)
from a2a_algorithms_common.http_service import create_algorithm_app

ALGORITHM_ID = "compliance_authorization_core"
VERSION = "1.0.0"
PORT = int(os.environ.get("PORT", "9021"))


def _predict(inputs: dict, params: dict) -> dict:
    return predict_compliance_authorization_core(inputs, params)


app = create_algorithm_app(
    ALGORITHM_ID,
    VERSION,
    "compliance",
    _predict,
    model_loaded_callable=lambda: True,
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
