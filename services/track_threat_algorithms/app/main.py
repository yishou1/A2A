#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.http_service import create_algorithm_app  # noqa: E402
from a2a_algorithms_common.service_predictors import (  # noqa: E402
    predict_graph_relation_reasoner,
    predict_multimodal_feature_fuser,
    predict_target_type_classifier,
    predict_track_state_updater,
    predict_trajectory_predictor,
)

VERSION = "1.0.0"
PORT = int(os.environ.get("PORT", "9022"))


def _model_loaded() -> bool:
    return True


app = FastAPI(title="track_threat_algorithms", version=VERSION)

for algorithm_id, task_family, predict_fn in (
    ("target_type_classifier", "classification", predict_target_type_classifier),
    ("trajectory_predictor", "forecasting", predict_trajectory_predictor),
    ("multimodal_feature_fuser", "feature_engineering", predict_multimodal_feature_fuser),
    ("track_state_updater", "tracking", predict_track_state_updater),
    ("graph_relation_reasoner", "graph_reasoning", predict_graph_relation_reasoner),
):
    app.mount(
        f"/{algorithm_id}",
        create_algorithm_app(
            algorithm_id,
            VERSION,
            task_family,
            predict_fn,
            model_loaded_callable=_model_loaded,
        ),
    )


@app.get("/health")
def service_health() -> dict:
    return {
        "ok": True,
        "status": "ready",
        "service": "track_threat_algorithms",
        "mounted_algorithms": [
            "target_type_classifier",
            "trajectory_predictor",
            "multimodal_feature_fuser",
            "track_state_updater",
            "graph_relation_reasoner",
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
