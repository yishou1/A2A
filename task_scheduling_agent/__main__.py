"""CLI: python -m task_scheduling_agent --input amos.json --output schedule.json"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_scheduling_agent.agent import TaskSchedulingAgent


def _load_yaml_config(path: Path | None) -> dict:
    if path is None:
        default = ROOT / "config" / "default.yaml"
        path = default if default.is_file() else None
    if path is None or not path.is_file():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Task Scheduling Agent — AMOS JSON → sensor/strike schedule"
    )
    parser.add_argument("--input", "-i", required=True, help="AMOS JSON input path")
    parser.add_argument("--output", "-o", default=None, help="Write schedule JSON")
    parser.add_argument(
        "--use-mock",
        action="store_true",
        help="Local heuristic mock scheduler (ignored when --algolib)",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Local MARL-PPO network (overrides --use-mock; ignored when --algolib)",
    )
    parser.add_argument(
        "--algolib",
        action="store_true",
        help="Use lzh-style algorithm library (GET /algorithms + POST /run)",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Enable small-model algorithm planning (requires ENABLE_LLM/TOOL_LLM_*)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="YAML config (default: config/default.yaml)",
    )
    parser.add_argument(
        "--algolib-base-url",
        default=None,
        help="Override ALGOLIB_BASE_URL (default http://127.0.0.1:8088)",
    )
    args = parser.parse_args(argv)

    cfg = _load_yaml_config(args.config)
    if args.algolib:
        cfg["backend"] = "algolib"
        cfg.setdefault("algorithm_library", {})
        cfg["algorithm_library"]["enabled"] = True
        cfg["algorithm_library"]["call_mode"] = "run"
        if args.algolib_base_url:
            cfg["algorithm_library"]["base_url"] = args.algolib_base_url
            os.environ["ALGOLIB_BASE_URL"] = args.algolib_base_url
        os.environ["TASK_SCHEDULING_BACKEND"] = "algolib"
    if args.llm:
        cfg.setdefault("tool_llm", {})
        cfg["tool_llm"]["enable"] = True
        cfg.setdefault("algorithm_planner", {})
        cfg["algorithm_planner"]["mode"] = "llm"
        os.environ["ENABLE_LLM"] = "true"
        os.environ["TASK_SCHEDULING_ALGORITHM_PLANNER"] = "llm"

    use_mock = True
    if args.real:
        use_mock = False
    elif args.use_mock:
        use_mock = True

    agent = TaskSchedulingAgent(use_mock=use_mock, config=cfg)
    result = agent.run_file(args.input, output_path=args.output)

    if args.output:
        print(f"[OK] wrote {args.output}")
        print(
            f"  sensors={len(result.get('sensor_assignments') or [])} "
            f"reattack={len(result.get('reattack_plan') or [])} "
            f"algorithm={result.get('algorithm')} "
            f"selected={result.get('selected_algorithms')} "
            f"llm_mode={(result.get('llm_plan') or {}).get('mode')}"
        )
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
