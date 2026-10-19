#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.http_service import create_algorithm_app
from a2a_algorithms_common.threat_priority_random_forest import (
    model_loaded,
    predict_threat_priorities,
)

ALGORITHM_ID = "threat_priority_random_forest"
VERSION = "1.0.0"
PORT = int(os.environ.get("PORT", "9032"))

app = create_algorithm_app(
    ALGORITHM_ID,
    VERSION,
    "classification",
    predict_threat_priorities,
    model_loaded_callable=model_loaded,
    extra_metadata={
        "algorithm_class": "M05",
        "algorithm_class_name": "random_forest",
        "model_family": "sklearn_random_forest_classifier",
        "owner_scope": "algorithm_library",
    },
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
