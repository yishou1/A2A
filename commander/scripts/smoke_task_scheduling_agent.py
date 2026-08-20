"""Smoke: AMOS sample JSON → TaskSchedulingAgent (mock)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from task_scheduling_agent import TaskSchedulingAgent

SAMPLE = ROOT / "examples" / "amos_schedule_inputs" / "sample_amos_request.json"
OUT = ROOT / "data" / "output" / "task_schedule" / "smoke_amos_demo.json"


def main() -> int:
    if not SAMPLE.is_file():
        print(f"[FAIL] missing sample: {SAMPLE}")
        return 2

    agent = TaskSchedulingAgent(use_mock=True)
    result = agent.run_file(SAMPLE, output_path=OUT)

    assignments = result.get("sensor_assignments") or []
    reattack = result.get("reattack_plan") or []
    algo = result.get("algorithm")
    schedule = result.get("task_schedule") or {}

    print(f"[ok] mission={result.get('mission_id')}")
    print(f"[ok] algorithm={algo}")
    print(f"[ok] sensors={len(assignments)} reattack={len(reattack)}")
    print(f"[ok] covered={result.get('covered_targets')}")
    print(f"[ok] wrote {OUT}")

    if not assignments:
        print("[FAIL] empty sensor_assignments")
        return 1
    if not algo:
        print("[FAIL] missing algorithm")
        return 1
    if "sensor_assignments" not in schedule:
        print("[FAIL] task_schedule missing sensor_assignments")
        return 1

    # 低电量平台应被标记 idle / 不可用 rationale
    low = next((a for a in assignments if a.get("sensor_id") == "UAV-LOWBAT"), None)
    if low is None:
        print("[WARN] UAV-LOWBAT not in assignments (may have been truncated by MAX_SENSORS)")
    else:
        print(f"[ok] UAV-LOWBAT priority={low.get('priority')} rationale={low.get('rationale')}")

    print("[OK] smoke_task_scheduling_agent passed")
    print(json.dumps({"algorithm": algo, "n_sensors": len(assignments), "n_reattack": len(reattack)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
