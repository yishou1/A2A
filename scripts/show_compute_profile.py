"""打印当前算力档位下各算法的模型配置。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml

from agent.config_profiles import apply_compute_profile, profile_summary, resolve_compute_profile


def main() -> int:
    parser = argparse.ArgumentParser(description="Show TIA compute profile (small/medium/large)")
    parser.add_argument("--config", type=Path, default=Path("config/default.yaml"))
    parser.add_argument(
        "--profile",
        choices=("auto", "small", "medium", "large"),
        help="临时覆盖档位（auto=按算力探测）",
    )
    args = parser.parse_args()

    cfg_path = args.config if args.config.is_absolute() else ROOT / args.config
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
    if args.profile:
        os.environ["TIA_COMPUTE_PROFILE"] = args.profile
    cfg = apply_compute_profile(cfg)
    inf = cfg.get("inference") or {}

    requested = inf.get("compute_profile_requested", resolve_compute_profile(cfg))
    resolved = inf.get("compute_profile", resolve_compute_profile(cfg))
    print(f"Requested: {requested}")
    print(f"Resolved:  {resolved}")
    if inf.get("compute_profile_auto_reason"):
        print(f"Auto reason: {inf['compute_profile_auto_reason']}")
    print(f"Config:   {cfg_path}\n")
    print(json.dumps(profile_summary(cfg), indent=2, ensure_ascii=False))
    print("\nCheckpoint overrides:")
    for key in (
        "odconv_checkpoint",
        "edl_checkpoint",
        "motr_checkpoint",
        "mamba_checkpoint",
        "supcon_checkpoint",
        "marl_checkpoint",
    ):
        if key in inf:
            print(f"  {key}: {inf[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
