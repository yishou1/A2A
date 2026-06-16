"""
仅建立并导出红蓝战场态势（不跑智能体推理）。

用法:
  python scripts/build_situation.py
  python scripts/build_situation.py --scenario scripts/simulation/scenarios/iron_valley_red_blue.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.simulation.situation import DEFAULT_SCENARIO, RedBlueSituation


def main() -> int:
    parser = argparse.ArgumentParser(description="导出红蓝战场态势 JSON")
    parser.add_argument(
        "--scenario",
        type=Path,
        default=DEFAULT_SCENARIO,
        help="场景 YAML（可编辑红蓝编制与阶段叙述）",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "output" / "situation",
    )
    args = parser.parse_args()

    sit = RedBlueSituation(args.scenario)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = args.output_dir / f"{sit.mission_id}-{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    overview_path = out_dir / "00_red_blue_overview.json"
    overview_path.write_text(
        json.dumps(sit.master_overview(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    phase_map = {
        "recon": "01_recon",
        "contact": "02_contact",
        "bda": "03_bda",
        "jammed": "04_jammed",
    }
    for phase_key, prefix in phase_map.items():
        snap = sit.snapshot_for_phase(phase_key)
        path = out_dir / f"{prefix}_situation.json"
        path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

    readme = out_dir / "README.txt"
    readme.write_text(
        "\n".join(
            [
                "红蓝战场态势导出目录",
                f"任务: {sit.mission_id} — {sit.operation_name}",
                f"场景源: {sit.scenario_path}",
                "",
                "文件说明:",
                "  00_red_blue_overview.json  — 战役级红蓝总览",
                "  01_recon_situation.json    — 侦察阶段态势",
                "  02_contact_situation.json  — 接触阶段态势",
                "  03_bda_situation.json      — 打击后评估态势",
                "  04_jammed_situation.json   — 强干扰阶段态势",
                "",
                "修改编制/位置/叙述: 编辑 scenarios/iron_valley_red_blue.yaml 后重新运行本脚本。",
                "联动智能体仿真: python scripts/run_simulation.py",
            ]
        ),
        encoding="utf-8",
    )

    print("红蓝态势已导出")
    print(f"  目录: {out_dir}")
    print(f"  总览: {overview_path.name}")
    print(f"  蓝方单位: {len(sit._blue.units)} | 红方单位: {len(sit._red.units)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
