#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.http_service import create_algorithm_app
from a2a_algorithms_common.service_predictors import predict_clustering_engine

ALGORITHM_ID = "clustering_engine"
VERSION = "1.0.0"
PORT = int(os.environ.get("PORT", "9031"))


app = create_algorithm_app(
    ALGORITHM_ID,
    VERSION,
    "clustering",
    predict_clustering_engine,
    model_loaded_callable=lambda: True,
    extra_metadata={
        "algorithm_class": "M01",
        "algorithm_class_name": "clustering",
        "implementations": ["kmeans", "dbscan"],
        "owner_scope": "algorithm_library",
    },
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
