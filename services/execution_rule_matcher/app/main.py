#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.http_service import create_algorithm_app
from a2a_algorithms_common.association_rules import rule_artifact_loaded
from a2a_algorithms_common.service_predictors import predict_execution_rule_matcher

ALGORITHM_ID = "execution_rule_matcher"
VERSION = "1.0.0"
PORT = int(os.environ.get("PORT", "9010"))


def _predict(inputs: dict, params: dict) -> dict:
    return predict_execution_rule_matcher(inputs, params)


app = create_algorithm_app(
    ALGORITHM_ID,
    VERSION,
    "decision",
    _predict,
    model_loaded_callable=rule_artifact_loaded,
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
