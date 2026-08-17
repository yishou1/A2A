from __future__ import annotations

import argparse
import contextlib
import io
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from commander_agent.agent_leases import AgentLeaseManager  # noqa: E402
from commander_agent.main import CommanderAgent  # noqa: E402


def progress(message: str):
    print(message, file=sys.__stdout__, flush=True)


BPEL_TEXT = """<?xml version="1.0" encoding="UTF-8"?>
<process name="IntegratedConcurrencyWorkflow">
  <sequence name="RootSequence">
    <flow name="ParallelPreparation">
      <invoke name="ArtilleryParallelBranch" partnerLink="ArtilleryAgent"
              operation="suppressBeachSector" dispatchMode="parallel"
              inputVariable="StrikeCoordinates" outputVariable="StrikeResult"/>
      <invoke name="EvaluatorBranch" partnerLink="EvaluatorAgent"
              operation="evaluateStrike" inputVariable="StrikeCoordinates"
              outputVariable="EvalScore"/>
    </flow>
    <invoke name="AssaultAfterPreparation" partnerLink="AssaultAgent"
            operation="captureBeachhead" inputVariable="StrikeCoordinates"
            outputVariable="AssaultResult"/>
  </sequence>
</process>
"""


class DemoRegistry:
    def __init__(self, workflow_index: int):
        base = 20000 + workflow_index * 100
        self.instances = []
        for offset in range(3):
            self.instances.append(
                {
                    "ip": f"10.{workflow_index}.0.{offset + 10}",
                    "port": base + offset,
                    "metadata": {"role": "artillery", "status": "idle"},
                }
            )

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


class Meter:
    def __init__(self):
        self.lock = threading.Lock()
        self.active_workflows = 0
        self.active_activities = 0
        self.active_agents = 0
        self.max_workflows = 0
        self.max_activities = 0
        self.max_agents = 0
        self.records = []
        self.t0 = time.perf_counter()

    def _record(self, layer, name, event, active):
        self.records.append((round(time.perf_counter() - self.t0, 3), layer, event, name, active))

    def start_workflow(self, workflow_id):
        with self.lock:
            self.active_workflows += 1
            self.max_workflows = max(self.max_workflows, self.active_workflows)
            self._record("workflow", workflow_id, "start", self.active_workflows)

    def end_workflow(self, workflow_id):
        with self.lock:
            self._record("workflow", workflow_id, "end", self.active_workflows)
            self.active_workflows -= 1

    def start_activity(self, name):
        with self.lock:
            self.active_activities += 1
            self.max_activities = max(self.max_activities, self.active_activities)
            self._record("activity", name, "start", self.active_activities)

    def end_activity(self, name):
        with self.lock:
            self._record("activity", name, "end", self.active_activities)
            self.active_activities -= 1

    def start_agent(self, name):
        with self.lock:
            self.active_agents += 1
            self.max_agents = max(self.max_agents, self.active_agents)
            self._record("agent", name, "start", self.active_agents)

    def end_agent(self, name):
        with self.lock:
            self._record("agent", name, "end", self.active_agents)
            self.active_agents -= 1


def parse_args():
    parser = argparse.ArgumentParser(description="Run one integrated three-layer concurrency demo")
    parser.add_argument("--workflow-count", type=int, default=3)
    parser.add_argument("--max-workflows", type=int, default=2)
    parser.add_argument("--max-activity-workers", type=int, default=2)
    parser.add_argument("--max-agent-workers", type=int, default=3)
    parser.add_argument("--details", action="store_true")
    return parser.parse_args()


def run_one_workflow(index: int, args, meter: Meter, gate: threading.Semaphore):
    workflow_id = f"integrated-wf-{index}"
    queued_at = time.perf_counter()
    with gate:
        wait_ms = round((time.perf_counter() - queued_at) * 1000, 1)
        progress(f"[WORKFLOW] {workflow_id} admitted_after_ms={wait_ms}")
        meter.start_workflow(workflow_id)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                workflow_path = Path(temp_dir) / "integrated_concurrency.bpel"
                workflow_path.write_text(BPEL_TEXT, encoding="utf-8")
                registry = DemoRegistry(index)
                lease_manager = AgentLeaseManager(registry)
                commander = CommanderAgent(
                    mode="remote",
                    workflow="bpel",
                    workflow_file=str(workflow_path),
                    workflow_id=workflow_id,
                    state_dir=temp_dir,
                    registry=registry,
                    lease_manager=lease_manager,
                    max_activity_workers=args.max_activity_workers,
                    max_agent_workers=args.max_agent_workers,
                    mock_eval_score=90,
                )

                original_delegate_parallel = commander.delegate_parallel_task
                original_delegate_task = commander.delegate_task

                def fake_remote_candidate(role, target, payload, stream=False, **_kwargs):
                    label = f"{workflow_id}:{target['ip']}:{target['port']}"
                    meter.start_agent(label)
                    time.sleep(0.35)
                    commander._remember_task_response(
                        payload["work_item"],
                        {
                            "status": "completed",
                            "output": {payload["output_hint"]: f"{label} done"},
                        },
                        role=role,
                        target=label,
                    )
                    meter.end_agent(label)
                    return True, None

                def metered_delegate_parallel(role, payload, stream=False):
                    meter.start_activity(f"{workflow_id}:{role}")
                    try:
                        return original_delegate_parallel(role, payload, stream=stream)
                    finally:
                        meter.end_activity(f"{workflow_id}:{role}")

                def metered_delegate_task(role, payload, stream=False):
                    meter.start_activity(f"{workflow_id}:{role}")
                    try:
                        time.sleep(0.35 if role == "evaluator" else 0.05)
                        output_value = payload["input"].get("mock_eval_score", f"{role}-result")
                        commander._remember_task_response(
                            payload["work_item"],
                            {
                                "status": "completed",
                                "output": {payload["output_hint"]: output_value},
                            },
                            role=role,
                            target="local-metered",
                        )
                        return True
                    finally:
                        meter.end_activity(f"{workflow_id}:{role}")

                commander._delegate_remote_candidate = fake_remote_candidate
                commander.delegate_parallel_task = metered_delegate_parallel
                commander.delegate_task = metered_delegate_task

                context = commander.run_bpel_workflow()
                status = context.get("workflow_status")
                completed_roles = context.get("completed_roles", [])
                progress(f"[WORKFLOW] {workflow_id} status={status} completed_roles={completed_roles}")
                if status != "completed":
                    raise AssertionError(f"{workflow_id} did not complete")
                return workflow_id
        finally:
            meter.end_workflow(workflow_id)


def main():
    args = parse_args()
    if args.max_workflows < 1:
        raise ValueError("--max-workflows must be at least 1")
    meter = Meter()
    gate = threading.Semaphore(args.max_workflows)
    progress("=== INTEGRATED THREE-LAYER CONCURRENCY DEMO ===")
    progress(
        f"[CONFIG] workflow_count={args.workflow_count} "
        f"max_workflows={args.max_workflows} "
        f"max_activity_workers={args.max_activity_workers} "
        f"max_agent_workers={args.max_agent_workers}"
    )

    captured = io.StringIO()
    redirect = contextlib.nullcontext() if args.details else contextlib.redirect_stdout(captured)
    with redirect:
        with ThreadPoolExecutor(max_workers=args.workflow_count) as executor:
            futures = [
                executor.submit(run_one_workflow, index + 1, args, meter, gate)
                for index in range(args.workflow_count)
            ]
            completed = [future.result() for future in as_completed(futures)]

    for offset, layer, event, name, active in meter.records:
        progress(f"[TIMING] t+{offset:.3f}s {layer:<8} {event:<5} active={active} {name}")

    progress("\n=== OBSERVED MAX CONCURRENCY ===")
    progress(f"[OBSERVED] max_parallel_workflows={meter.max_workflows}")
    progress(f"[OBSERVED] max_parallel_activities={meter.max_activities}")
    progress(f"[OBSERVED] max_parallel_agent_calls={meter.max_agents}")
    progress(f"[COMPLETED] workflows={sorted(completed)}")

    if meter.max_workflows != min(args.max_workflows, args.workflow_count):
        raise AssertionError("multi-workflow concurrency limit was not observed")
    if meter.max_activities < min(args.max_activity_workers, 2):
        raise AssertionError("activity concurrency was not observed")
    if meter.max_agents < min(args.max_agent_workers, 3):
        raise AssertionError("same-role Agent concurrency was not observed")

    progress("[PASS] integrated demo observed all three concurrency layers in one run")


if __name__ == "__main__":
    main()
