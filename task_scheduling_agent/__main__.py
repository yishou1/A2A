"""CLI: python -m task_scheduling_agent --input amos.json --output schedule.json"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_scheduling_agent.agent import TaskSchedulingAgent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Task Scheduling Agent — AMOS JSON → sensor/strike schedule"
    )
    parser.add_argument(
        "--input",
        "-i",
        required=True,
        help="AMOS JSON input path",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write schedule JSON (default: stdout only)",
    )
    parser.add_argument(
        "--use-mock",
        action="store_true",
        help="Use heuristic mock scheduler (recommended without checkpoint)",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Use MARL-PPO network (overrides --use-mock)",
    )
    args = parser.parse_args(argv)

    use_mock = True
    if args.real:
        use_mock = False
    elif args.use_mock:
        use_mock = True

    agent = TaskSchedulingAgent(use_mock=use_mock)
    result = agent.run_file(args.input, output_path=args.output)

    if args.output:
        print(f"[OK] wrote {args.output}")
        print(
            f"  sensors={len(result.get('sensor_assignments') or [])} "
            f"reattack={len(result.get('reattack_plan') or [])} "
            f"algorithm={result.get('algorithm')}"
        )
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
