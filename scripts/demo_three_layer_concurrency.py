from __future__ import annotations

import argparse
import contextlib
import io
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from commander_agent.agent_leases import AgentLeaseManager  # noqa: E402
from commander_agent.main import CommanderAgent  # noqa: E402
from scripts.demo_workflow_manager import demo_workflow_pool  # noqa: E402


class DemoRegistry:
    def __init__(self, role: str, ports: list[int]):
        self.instances = [
            {
                "ip": f"10.0.0.{index + 10}",
                "port": port,
                "metadata": {"role": role, "status": "idle"},
            }
            for index, port in enumerate(ports)
        ]

    def discover_service(self, _service_name, required_tags=None):
        required_tags = required_tags or {}
        return [
            instance
            for instance in self.instances
            if all(
                instance.get("metadata", {}).get(key) == value
                for key, value in required_tags.items()
            )
        ]

    def update_instance_metadata(
        self,
        _service_name,
        instance,
        metadata_updates=None,
        remove_keys=None,
    ):
        metadata = instance.setdefault("metadata", {})
        metadata.update(metadata_updates or {})
        for key in remove_keys or []:
            metadata.pop(key, None)
        return metadata


class ConcurrencyMeter:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.records = []

    def start(self, name: str):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.records.append((name, "start", time.perf_counter(), self.active))

    def finish(self, name: str):
        with self.lock:
            self.records.append((name, "end", time.perf_counter(), self.active))
            self.active -= 1

    def compact_records(self):
        base = min(record[2] for record in self.records)
        return [
            (name, event, round(timestamp - base, 3), active)
            for name, event, timestamp, active in self.records
        ]


def parse_args():
    parser = argparse.ArgumentParser(description="Demonstrate the three concurrency layers")
    parser.add_argument("--max-agent-workers", type=int, default=3)
    parser.add_argument("--max-activity-workers", type=int, default=2)
    parser.add_argument("--max-workflows", type=int, default=2)
    parser.add_argument(
        "--state-dir",
        default=str(PROJECT_ROOT / ".a2a_state" / "three_layer_concurrency_demo"),
    )
    parser.add_argument("--details", action="store_true")
    return parser.parse_args()


def demo_same_role_agent_concurrency(args):
    print("=== LAYER 1: SAME-ROLE AGENT CONCURRENCY ===")
    registry = DemoRegistry("artillery", [19013, 19014, 19015])
    lease_manager = AgentLeaseManager(registry)
    meter = ConcurrencyMeter()

    with tempfile.TemporaryDirectory() as temp_dir:
        commander = CommanderAgent(
            mode="remote",
            workflow_id="demo-agent-concurrency",
            state_dir=temp_dir,
            registry=registry,
            lease_manager=lease_manager,
            max_agent_workers=args.max_agent_workers,
        )

        def fake_remote_candidate(role, target, payload, stream=False, **_kwargs):
            label = f"{target['ip']}:{target['port']}"
            meter.start(label)
            time.sleep(0.4)
            commander._remember_task_response(
                payload["work_item"],
                {"status": "completed", "output": {"strike_result": f"{label} done"}},
                role=role,
                target=label,
            )
            meter.finish(label)
            return True, None

        commander._delegate_remote_candidate = fake_remote_candidate
        output = io.StringIO()
        redirect = contextlib.nullcontext() if args.details else contextlib.redirect_stdout(output)
        with redirect:
            success = commander.delegate_parallel_task(
                "artillery",
                {
                    "workflow_id": "demo-agent-concurrency",
                    "work_item": "demo-agent-concurrency:artillery",
                },
                stream=False,
            )

    targets = [f"{item['ip']}:{item['port']}" for item in registry.instances]
    statuses = [item["metadata"]["status"] for item in registry.instances]
    print(f"[TARGETS] {targets}")
    print(f"[WORKERS] max_agent_workers={args.max_agent_workers}")
    print(f"[OBSERVED] max_parallel_agent_calls={meter.max_active}")
    for name, event, offset, active in meter.compact_records():
        print(f"[TIMING] t+{offset:.3f}s {event:<5} {name} active={active}")
    print(f"[LEASES] final_statuses={statuses}")
    if not success or meter.max_active < min(args.max_agent_workers, len(targets)):
        raise AssertionError("same-role Agent concurrency was not observed")
    if statuses != ["idle", "idle", "idle"]:
        raise AssertionError(f"leases were not released: {statuses}")
    print("[PASS] same-role Agent dispatch used parallel workers and released all leases\n")


def demo_activity_concurrency(args):
    print("=== LAYER 2: BPEL ACTIVITY CONCURRENCY ===")
    bpel_text = """<?xml version="1.0" encoding="UTF-8"?>
<process name="DemoActivityConcurrencyWorkflow">
  <sequence name="RootSequence">
    <flow name="ParallelAssessment">
      <invoke name="ReconBranch" partnerLink="ReconAgent" operation="scanBeachDefenses" inputVariable="Sector_A" outputVariable="ReconReport"/>
      <invoke name="EvalBranch" partnerLink="EvaluatorAgent" operation="evaluateStrike" inputVariable="StrikeCoordinates" outputVariable="EvalScore"/>
    </flow>
    <invoke name="AssaultAfterFlow" partnerLink="AssaultAgent" operation="captureBeachhead" inputVariable="StrikeCoordinates" outputVariable="AssaultResult"/>
  </sequence>
</process>
"""
    meter = ConcurrencyMeter()
    with tempfile.TemporaryDirectory() as temp_dir:
        workflow_path = Path(temp_dir) / "activity_concurrency.bpel"
        workflow_path.write_text(bpel_text, encoding="utf-8")
        commander = CommanderAgent(
            mode="local",
            workflow="bpel",
            workflow_file=str(workflow_path),
            workflow_id="demo-activity-concurrency",
            state_dir=temp_dir,
            mock_eval_score=88,
            max_activity_workers=args.max_activity_workers,
        )

        def fake_delegate(role, payload, stream=False):
            meter.start(role)
            time.sleep(0.4 if role in {"recon", "evaluator"} else 0.05)
            output_value = payload["input"].get("mock_eval_score", f"{role}-result")
            commander._remember_task_response(
                payload["work_item"],
                {"status": "completed", "output": {payload["output_hint"]: output_value}},
                role=role,
                target="local-demo",
            )
            meter.finish(role)
            return True

        commander.delegate_task = fake_delegate
        output = io.StringIO()
        redirect = contextlib.nullcontext() if args.details else contextlib.redirect_stdout(output)
        with redirect:
            context = commander.run_bpel_workflow()

    flow_events = [
        event
        for event in context.get("trace", [])
        if event.get("event_type")
        in {"flow_activity_started", "flow_child_activity_scheduled", "flow_child_activity_finished"}
    ]
    print(f"[WORKERS] max_activity_workers={args.max_activity_workers}")
    print(f"[OBSERVED] max_parallel_activities={meter.max_active}")
    for name, event, offset, active in meter.compact_records():
        print(f"[TIMING] t+{offset:.3f}s {event:<5} activity_role={name} active={active}")
    print(
        "[TRACE] "
        + " | ".join(
            f"{event['event_type']}:{event.get('child_role') or event.get('execution_mode')}"
            for event in flow_events
        )
    )
    if context.get("workflow_status") != "completed":
        raise AssertionError("activity concurrency workflow did not complete")
    if meter.max_active < min(args.max_activity_workers, 2):
        raise AssertionError("BPEL flow activity concurrency was not observed")
    print("[PASS] BPEL flow scheduled independent activities concurrently\n")


def demo_multi_workflow_concurrency(args):
    print("=== LAYER 3: MULTI-WORKFLOW CONCURRENCY ===")
    state_dir = Path(args.state_dir)
    if state_dir.exists():
        shutil.rmtree(state_dir)
    manager_args = SimpleNamespace(
        state_dir=str(state_dir),
        manager_port=0,
        max_workflows=args.max_workflows,
        poll_interval=0.1,
        timeout=30.0,
        details=args.details,
    )
    demo_workflow_pool(manager_args)
    print("\n[PASS] workflow manager enforced running/queued concurrency limit\n")


def main():
    args = parse_args()
    demo_same_role_agent_concurrency(args)
    demo_activity_concurrency(args)
    demo_multi_workflow_concurrency(args)
    print("=== THREE-LAYER CONCURRENCY DEMO PASSED ===")


if __name__ == "__main__":
    main()
