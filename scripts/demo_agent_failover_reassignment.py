from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a2a_protocol.messages import build_task_response  # noqa: E402
from commander_agent.agent_leases import AgentLeaseManager  # noqa: E402
from commander_agent.main import CommanderAgent  # noqa: E402


class DemoRegistry:
    def __init__(self):
        now = time.time()
        self.instances = [
            {
                "ip": "10.0.0.11",
                "port": 8012,
                "metadata": {
                    "name": "Recon_Primary",
                    "role": "recon",
                    "status": "idle",
                    "heartbeat_ts": now,
                },
            },
            {
                "ip": "10.0.0.12",
                "port": 8012,
                "metadata": {
                    "name": "Recon_Backup",
                    "role": "recon",
                    "status": "idle",
                    "heartbeat_ts": now,
                },
            },
        ]
        self.heartbeat_grace_seconds = 0.2

    def discover_service(self, service_name, required_tags=None):
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
        service_name,
        instance,
        metadata_updates=None,
        remove_keys=None,
    ):
        metadata = instance.setdefault("metadata", {})
        metadata.update(metadata_updates or {})
        for key in remove_keys or []:
            metadata.pop(key, None)
        return metadata

    def find_instance(self, service_name, target):
        for instance in self.instances:
            if instance["ip"] == target["ip"] and instance["port"] == target["port"]:
                return instance
        return None

    def is_instance_fresh(self, instance):
        heartbeat_ts = float(instance.get("metadata", {}).get("heartbeat_ts", 0))
        return (time.time() - heartbeat_ts) <= self.heartbeat_grace_seconds

    def reset(self):
        now = time.time()
        for instance in self.instances:
            metadata = instance["metadata"]
            metadata["status"] = "idle"
            metadata["heartbeat_ts"] = now
            for key in [
                "lease_workflow_id",
                "lease_work_item",
                "lease_acquired_at",
                "unavailable_workflow_id",
                "unavailable_work_item",
                "unavailable_at",
                "unavailable_reason",
            ]:
                metadata.pop(key, None)

    def snapshot(self):
        return [
            {
                "agent": instance["metadata"].get("name"),
                "address": f"{instance['ip']}:{instance['port']}",
                "role": instance["metadata"].get("role"),
                "status": instance["metadata"].get("status"),
                "lease_workflow_id": instance["metadata"].get("lease_workflow_id"),
                "unavailable_reason": instance["metadata"].get("unavailable_reason"),
            }
            for instance in self.instances
        ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Demonstrate Agent failover reassignment when one Agent is down."
    )
    parser.add_argument(
        "--workflow-id",
        default="demo-agent-failover",
        help="Workflow id used by the demo checkpoint.",
    )
    parser.add_argument(
        "--state-dir",
        default="/tmp/a2a-agent-failover-demo-state",
        help="Directory used to persist the demo checkpoint.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the demo state directory before running.",
    )
    return parser.parse_args()


def print_json(label: str, payload) -> None:
    print(f"\n{label}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def interesting_trace(context: dict) -> list[dict]:
    wanted = {
        "agent_call_failed",
        "agent_heartbeat_lost",
        "agent_marked_unavailable",
        "agent_failover_reassigning",
        "agent_late_response_ignored",
        "agent_call_completed",
        "agent_result_applied",
    }
    return [
        event
        for event in context.get("trace", [])
        if event.get("event_type") in wanted
    ]


def main() -> None:
    args = parse_args()
    state_dir = Path(args.state_dir).expanduser().resolve()
    if args.reset and state_dir.exists():
        shutil.rmtree(state_dir)

    registry = DemoRegistry()
    lease_manager = AgentLeaseManager(registry)
    commander = CommanderAgent(
        mode="remote",
        workflow="dynamic",
        workflow_id=args.workflow_id,
        state_dir=str(state_dir),
        registry=registry,
        lease_manager=lease_manager,
        max_retries=0,
        request_timeout=1.0,
    )
    commander.lease_heartbeat_check_interval = 0.05

    def remember_backup_result(role, target, payload):
        metadata = target.get("metadata", {})
        name = metadata.get("name")
        address = f"{target['ip']}:{target['port']}"
        response = build_task_response(
            workflow_id=payload.get("workflow_id"),
            work_item=payload.get("work_item"),
            agent=name,
            role=role,
            command=payload.get("command"),
            status="completed",
            output={
                payload.get("output_hint") or "recon_report": (
                    "Backup recon completed the reassigned scan."
                )
            },
            metrics={"simulated": True},
            message=f"{name} completed reassigned task",
        )
        commander._remember_task_response(
            payload.get("work_item"),
            response,
            role=role,
            target=address,
        )

    def simulated_remote_candidate(role, target, payload, stream=False, **kwargs):
        metadata = target.get("metadata", {})
        name = metadata.get("name")
        address = f"{target['ip']}:{target['port']}"
        print(f"[CALL] role={role} target={name} address={address}")
        if name == "Recon_Primary":
            print("[DOWN] Recon_Primary is down; simulated connection refused.")
            return False, requests.exceptions.ConnectionError("connection refused")

        remember_backup_result(role, target, payload)
        print("[RECOVERED] Recon_Backup accepted and completed the reassigned task.")
        return True, None

    commander._delegate_remote_candidate = simulated_remote_candidate

    print("\n=== A2A AGENT FAILOVER REASSIGNMENT DEMO ===")
    print_json("[PHASE 1] Initial Agent registry", registry.snapshot())

    context = commander.workflow_context
    payload, stream = commander.build_task_payload("recon", context, activatity_index=1)

    print("\n[PHASE 2] Commander dispatches recon task.")
    success = commander.delegate_task("recon", payload, stream=stream)
    if not success:
        raise RuntimeError("Demo failed: task was not reassigned to the backup Agent.")

    commander.apply_agent_result(
        "recon",
        success,
        context,
        work_item=payload["work_item"],
        output_key=payload.get("output_hint"),
    )
    commander._save_workflow_checkpoint(
        context,
        status="running",
        current_activatity={
            "activatity_index": 1,
            "activity_index": 1,
            "type": "agent",
            "role": "recon",
            "status": "completed",
            "work_item": payload["work_item"],
        },
    )

    idle_recon = registry.discover_service(
        "A2A-Agent",
        {"role": "recon", "status": "idle"},
    )

    print_json("[PHASE 3] Registry after failover", registry.snapshot())
    print_json(
        "[PHASE 4] Idle recon candidates for future tasks",
        [
            {
                "agent": instance["metadata"].get("name"),
                "address": f"{instance['ip']}:{instance['port']}",
            }
            for instance in idle_recon
        ],
    )
    print_json("[PHASE 5] Failover trace", interesting_trace(context))
    print_json(
        "[RESULT] Workflow context summary",
        {
            "workflow_id": commander.workflow_id,
            "work_item": payload["work_item"],
            "success": success,
            "recon_report": context.get("recon_report"),
            "completed_roles": context.get("completed_roles"),
            "checkpoint": str(commander.state_store.state_path(commander.workflow_id)),
        },
    )

    if registry.instances[0]["metadata"].get("status") != "unavailable":
        raise AssertionError("Primary Agent was not marked unavailable.")
    if context.get("recon_report") != "Backup recon completed the reassigned scan.":
        raise AssertionError("Backup Agent result was not applied to workflow context.")

    print("\n[PASS] Down Agent was isolated and the task was reassigned.")

    print("\n=== ACTIVE HEARTBEAT WATCHER DEMO ===")
    registry.reset()
    commander._last_task_responses.clear()
    context["agent_results"].clear()
    context["trace"].clear()
    context["completed_roles"].clear()
    context["recon_report"] = None
    print_json("[PHASE 1] Registry reset before active heartbeat scenario", registry.snapshot())

    def heartbeat_loss_candidate(role, target, payload, stream=False, **kwargs):
        metadata = target.get("metadata", {})
        name = metadata.get("name")
        address = f"{target['ip']}:{target['port']}"
        lease = kwargs.get("lease")
        print(f"[CALL] role={role} target={name} address={address}")
        if name == "Recon_Primary":
            metadata["heartbeat_ts"] = time.time() - 10
            print("[HEARTBEAT LOST] Recon_Primary stops heartbeating while task is running.")
            time.sleep(0.3)
            print("[LATE] Recon_Primary returned after failover.")
            if commander._lease_allows_response(
                lease,
                address,
                payload.get("work_item"),
                role,
            ):
                print("[UNEXPECTED] Recon_Primary late result was still accepted.")
            else:
                print("[IGNORED] Recon_Primary late result was rejected by lease/heartbeat guard.")
            return True, None

        remember_backup_result(role, target, payload)
        print("[RECOVERED] Recon_Backup completed after heartbeat-triggered reassignment.")
        return True, None

    commander._delegate_remote_candidate = heartbeat_loss_candidate
    payload, stream = commander.build_task_payload("recon", context, activatity_index=2)

    print("\n[PHASE 2] Commander dispatches recon task and watches active lease heartbeat.")
    success = commander.delegate_task("recon", payload, stream=stream)
    if not success:
        raise RuntimeError("Demo failed: heartbeat loss did not trigger reassignment.")

    commander.apply_agent_result(
        "recon",
        success,
        context,
        work_item=payload["work_item"],
        output_key=payload.get("output_hint"),
    )
    commander._save_workflow_checkpoint(
        context,
        status="running",
        current_activatity={
            "activatity_index": 2,
            "activity_index": 2,
            "type": "agent",
            "role": "recon",
            "status": "completed",
            "work_item": payload["work_item"],
        },
    )

    deadline = time.time() + 1.0
    while time.time() < deadline and not any(
        event.get("event_type") == "agent_late_response_ignored"
        for event in context.get("trace", [])
    ):
        time.sleep(0.02)

    print_json("[PHASE 3] Registry after heartbeat-triggered failover", registry.snapshot())
    print_json("[PHASE 4] Heartbeat failover trace", interesting_trace(context))
    print_json(
        "[RESULT] Active heartbeat watcher summary",
        {
            "workflow_id": commander.workflow_id,
            "work_item": payload["work_item"],
            "success": success,
            "recon_report": context.get("recon_report"),
            "checkpoint": str(commander.state_store.state_path(commander.workflow_id)),
        },
    )
    if not any(
        event.get("event_type") == "agent_late_response_ignored"
        for event in context.get("trace", [])
    ):
        raise AssertionError("Late primary Agent response was not ignored.")
    print("\n[PASS] Active heartbeat loss triggered reassignment before the original call returned.")


if __name__ == "__main__":
    main()
