from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bpel_workflow import BPELWorkflowCatalog  # noqa: E402
from commander_agent.main import CommanderAgent  # noqa: E402


DEFAULT_WORKFLOWS = [
    ("beachhead_workflow", 75),
    ("reinforced_beachhead_workflow", 85),
    ("quick_strike_workflow", 75),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Display and run selectable BPEL workflows")
    parser.add_argument(
        "--state-dir",
        default="/tmp/a2a-bpel-demo-state",
        help="Directory used to persist demo checkpoints",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Maximum concurrent same-role agent assignments",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Print complete Commander execution logs",
    )
    return parser.parse_args()


def print_work_list(definition, workflow_id: str) -> None:
    print("  work_list:")
    print("  idx  type       role        dispatch   operation")
    print("  ---  ---------  ----------  ---------  ------------------------")
    for item in definition.initial_work_list(workflow_id):
        print(
            f"  {item['activatity_index']:>3}  "
            f"{item['type']:<9}  "
            f"{(item['role'] or '-'): <10}  "
            f"{item['dispatch_mode']:<9}  "
            f"{item['operation'] or '-'}"
        )


def summarize_workflow(definition) -> None:
    print(f"\n[BPEL] {definition.process_name}")
    print(f"  file: {definition.source_path.name}")
    print_work_list(definition, f"preview-{definition.source_path.stem}")


def run_workflow(
    *,
    workflow_ref: str,
    mock_eval_score: int,
    state_dir: Path,
    max_workers: int,
    details: bool,
) -> dict:
    workflow_id = f"demo-{workflow_ref}"
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        commander = CommanderAgent(
            mode="local",
            workflow="bpel",
            workflow_file=workflow_ref,
            workflow_id=workflow_id,
            state_dir=str(state_dir),
            mock_eval_score=mock_eval_score,
            max_workers=max_workers,
        )
        context = commander.run_bpel_workflow()

    if details:
        print(output.getvalue().rstrip())

    invoked_items = [
        item
        for item in context["work_list"]
        if item["type"] == "invoke" and item["status"] == "completed"
    ]
    skipped_items = [
        item["operation"] or item["name"]
        for item in context["work_list"]
        if item["status"] == "skipped"
    ]
    execution_chain = [
        f"{item['role']}[{item['dispatch_mode']}]"
        for item in invoked_items
    ]

    return {
        "workflow_id": workflow_id,
        "workflow": commander.bpel_definition.process_name,
        "file": commander.bpel_definition.source_path.name,
        "mock_eval_score": mock_eval_score,
        "workflow_status": context["workflow_status"],
        "execution_chain": execution_chain,
        "skipped_activatities": skipped_items,
        "checkpoint": str(commander.state_store.state_path(workflow_id)),
    }


def main() -> None:
    args = parse_args()
    state_dir = Path(args.state_dir).expanduser().resolve()
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    catalog = BPELWorkflowCatalog(PROJECT_ROOT)
    definitions = [catalog.load(str(path)) for path in catalog.discover()]

    print("=== AVAILABLE BPEL WORKFLOWS ===")
    for definition in definitions:
        summarize_workflow(definition)

    print("\n=== RUN SELECTED BPEL WORKFLOWS ===")
    results = []
    for workflow_ref, mock_eval_score in DEFAULT_WORKFLOWS:
        print(f"\n[RUN] workflow={workflow_ref} mock_eval_score={mock_eval_score}")
        result = run_workflow(
            workflow_ref=workflow_ref,
            mock_eval_score=mock_eval_score,
            state_dir=state_dir,
            max_workers=args.max_workers,
            details=args.details,
        )
        results.append(result)
        print(f"  process: {result['workflow']}")
        print(f"  status: {result['workflow_status']}")
        print(f"  execution_chain: {' -> '.join(result['execution_chain'])}")
        print(f"  checkpoint: {result['checkpoint']}")

    print("\n=== SUMMARY ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
