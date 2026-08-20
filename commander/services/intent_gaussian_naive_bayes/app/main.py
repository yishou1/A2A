#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.http_service import create_algorithm_app
from a2a_algorithms_common.intent_gaussian_naive_bayes import model_loaded, predict_intents

ALGORITHM_ID = "intent_gaussian_naive_bayes"
VERSION = "1.0.0"
PORT = int(os.environ.get("PORT", "9033"))

app = create_algorithm_app(
    ALGORITHM_ID,
    VERSION,
    "classification",
    predict_intents,
    model_loaded_callable=model_loaded,
    extra_metadata={
        "algorithm_class": "M07",
        "algorithm_class_name": "naive_bayes",
        "model_family": "sklearn_gaussian_naive_bayes",
        "owner_scope": "algorithm_library",
    },
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
