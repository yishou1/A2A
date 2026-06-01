from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from commander_agent.main import CommanderAgent  # noqa: E402
from workflow_state_store import WorkflowStateStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Demo workflow resume after restart")
    parser.add_argument("--workflow-id", default="workflow-restart-demo", help="Workflow checkpoint id to reuse")
    parser.add_argument(
        "--state-dir",
        default=str(PROJECT_ROOT / ".a2a_state" / "workflows"),
        help="Directory used to persist workflow checkpoints",
    )
    parser.add_argument(
        "--first-max-steps",
        type=int,
        default=2,
        help="How many steps to run before simulating a restart",
    )
    parser.add_argument(
        "--mock-eval-score",
        type=int,
        default=75,
        help="Mock evaluation score used to avoid external LLM calls",
    )
    parser.add_argument(
        "--mock-decision",
        default="ASSAULT",
        choices=["ASSAULT", "RE-PLAN"],
        help="Mock commander decision used to avoid external LLM calls",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete any existing checkpoint before running the demo",
    )
    return parser.parse_args()


def summarize_state(label: str, state: dict) -> None:
    context = state.get("context", {})
    payload = {
        "label": label,
        "workflow_id": state.get("workflow_id"),
        "status": state.get("status"),
        "workflow_status": context.get("workflow_status"),
        "workflow_activatity": context.get("workflow_activatity"),
        "current_activatity": context.get("current_activatity"),
        "last_work_item": context.get("last_work_item"),
        "completed_roles": context.get("completed_roles"),
        "battle_log_count": len(context.get("battle_log", [])),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    args = parse_args()
    state_dir = Path(args.state_dir)
    store = WorkflowStateStore(str(state_dir))

    if args.reset and store.exists(args.workflow_id):
        store.delete(args.workflow_id)
        print(f"[RESET] Removed existing checkpoint: {store.state_path(args.workflow_id)}")

    print("[PHASE 1] Start a fresh workflow and stop halfway.")
    first = CommanderAgent(
        mode="local",
        workflow="dynamic",
        workflow_id=args.workflow_id,
        state_dir=str(state_dir),
        resume=False,
        mock_eval_score=args.mock_eval_score,
        mock_decision=args.mock_decision,
    )
    first_context = first.run_dynamic_battle_scenario(max_steps=args.first_max_steps)
    first_state = first.state_store.load(args.workflow_id)
    summarize_state("after_first_run", first_state)

    print("\n[PHASE 2] Simulate a process restart and resume the same workflow id.")
    second = CommanderAgent(
        mode="local",
        workflow="dynamic",
        workflow_id=args.workflow_id,
        state_dir=str(state_dir),
        resume=True,
        mock_eval_score=args.mock_eval_score,
        mock_decision=args.mock_decision,
    )
    second_context = second.run_dynamic_battle_scenario(max_steps=10)
    second_state = second.state_store.load(args.workflow_id)
    summarize_state("after_resume", second_state)

    print("\n[RESULT] Final workflow context:")
    print(json.dumps(second_context, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
