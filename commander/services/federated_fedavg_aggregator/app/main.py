#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.federated_fedavg import (
    federated_average,
    implementation_loaded,
)
from a2a_algorithms_common.http_service import create_algorithm_app

ALGORITHM_ID = "federated_fedavg_aggregator"
VERSION = "1.0.0"
PORT = int(os.environ.get("PORT", "9034"))

app = create_algorithm_app(
    ALGORITHM_ID,
    VERSION,
    "federated_learning",
    federated_average,
    model_loaded_callable=implementation_loaded,
    extra_metadata={
        "algorithm_class": "M12",
        "algorithm_class_name": "federated_learning",
        "aggregation_protocol": "fedavg_weighted_by_sample_count",
        "owner_scope": "algorithm_library",
    },
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
