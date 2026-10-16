"""Fail early when a requested algorithm mode lacks repository model assets."""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REQUIRED_REPOSITORY_ASSETS = (
    ROOT / "algorithmrepo/models/checkpoints/edl_head_s.safetensors",
    ROOT / "algorithmrepo/models/checkpoints/mamba_fusion_s.safetensors",
    ROOT / "algorithmrepo/models/checkpoints/marl_ppo_scheduler_s.safetensors",
    ROOT / "algorithmrepo/models/checkpoints/supcon_meta_s.safetensors",
    ROOT / "algorithmrepo/models/track_threat/st_gnn_ship_kaggle_v1/model.ts",
)
UNPUBLISHED_TIA_WEIGHTS = (
    ROOT / "commander/models/checkpoints/odconv_refiner.pt",
    ROOT / "commander/models/checkpoints/motr_tracker_battlefield.pt",
)


def main() -> None:
    missing = [str(path.relative_to(ROOT)) for path in REQUIRED_REPOSITORY_ASSETS if not path.is_file()]
    if missing:
        raise RuntimeError("Required checked-in algorithm assets are missing: " + ", ".join(missing))
    use_mock = os.environ.get("TIA_USE_MOCK", "1").lower() in {"1", "true", "yes"}
    if not use_mock:
        missing_real = [str(path.relative_to(ROOT)) for path in UNPUBLISHED_TIA_WEIGHTS if not path.is_file()]
        if missing_real:
            raise RuntimeError(
                "TIA_USE_MOCK=false needs unpublished perception weights: " + ", ".join(missing_real)
            )
        print("[models] real TIA perception weights are present", flush=True)
    else:
        print("[models] TIA algorithm mock mode is active; Qwen planning remains real", flush=True)


if __name__ == "__main__":
    main()
