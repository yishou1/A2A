#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.conditional_tabular_gan import (
    generate_conditional_samples,
    model_loaded,
)
from a2a_algorithms_common.http_service import create_algorithm_app

ALGORITHM_ID = "conditional_tabular_gan"
VERSION = "1.0.0"
PORT = int(os.environ.get("PORT", "9035"))

app = create_algorithm_app(
    ALGORITHM_ID,
    VERSION,
    "generation",
    generate_conditional_samples,
    model_loaded_callable=model_loaded,
    extra_metadata={
        "algorithm_class": "M08",
        "algorithm_class_name": "generative_adversarial_network",
        "model_family": "pytorch_conditional_gan",
        "owner_scope": "algorithm_library",
    },
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
