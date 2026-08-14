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

TRACK_THREAT_ALGORITHMS = (
    ("multimodal_feature_fuser", "feature_engineering", "M03", "多模态融合", predict_multimodal_feature_fuser),
    ("target_type_classifier", "classification", "M04", "特征编码与分类", predict_target_type_classifier),
    ("track_state_updater", "tracking", "M05", "多目标跟踪与定位", predict_track_state_updater),
    ("trajectory_predictor", "forecasting", "M06", "时间序列预测", predict_trajectory_predictor),
    ("graph_relation_reasoner", "graph_reasoning", "M07", "图神经网络", predict_graph_relation_reasoner),
)


for algorithm_id, task_family, algorithm_class, algorithm_class_name, predict_fn in TRACK_THREAT_ALGORITHMS:
    app.mount(
        f"/{algorithm_id}",
        create_algorithm_app(
            algorithm_id,
            VERSION,
            task_family,
            predict_fn,
            model_loaded_callable=_model_loaded,
            extra_metadata={
                "algorithm_class": algorithm_class,
                "algorithm_class_name": algorithm_class_name,
                "owner_scope": "track_threat_agent",
                "safety_boundary": "simulation situation awareness only; no weapon control or engagement advice",
            },
        ),
    )


@app.get("/health")
def service_health() -> dict:
    return {
        "ok": True,
        "status": "ready",
        "service": "track_threat_algorithms",
        "mounted_algorithms": [item[0] for item in TRACK_THREAT_ALGORITHMS],
        "algorithm_classes": {
            item[0]: {"algorithm_class": item[2], "algorithm_class_name": item[3]}
            for item in TRACK_THREAT_ALGORITHMS
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
