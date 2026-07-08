from __future__ import annotations

import argparse
import contextlib
import os
import sys
import time
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from registry.nacos_manager import NacosRegistry, get_host_ip  # noqa: E402


SERVICE_NAME = "A2A-Nacos-UI-Demo"


def parse_args():
    parser = argparse.ArgumentParser(description="Hold Nacos metadata states for UI demonstration")
    parser.add_argument("--nacos-addr", default=os.environ.get("NACOS_ADDR", "127.0.0.1:8848"))
    parser.add_argument("--hold-seconds", type=float, default=8.0)
    parser.add_argument("--keep", action="store_true", help="Do not delete demo instances at the end")
    return parser.parse_args()


def nacos_base_url(address: str) -> str:
    address = address.split(",", 1)[0].strip().rstrip("/")
    return address if address.startswith(("http://", "https://")) else f"http://{address}"


def delete_instance(nacos_addr: str, ip: str, port: int):
    with contextlib.suppress(requests.RequestException):
        response = requests.delete(
            f"{nacos_base_url(nacos_addr)}/nacos/v1/ns/instance",
            params={
                "serviceName": SERVICE_NAME,
                "ip": ip,
                "port": port,
                "clusterName": "DEFAULT",
                "groupName": "DEFAULT_GROUP",
                "ephemeral": "true",
            },
            timeout=3,
        )
        response.raise_for_status()


def hold(label: str, seconds: float):
    print(f"\n[UI HOLD] {label}")
    print(f"Open: http://127.0.0.1:8848/nacos/")
    print(f"Service: {SERVICE_NAME}")
    print(f"Waiting {seconds:.0f}s so you can inspect Nacos UI...")
    time.sleep(seconds)


def main():
    args = parse_args()
    registry = NacosRegistry(server_addresses=args.nacos_addr)
    ip = get_host_ip()
    primary = {"ip": ip, "port": 19101, "metadata": {"role": "recon", "status": "idle"}}
    backup = {"ip": ip, "port": 19102, "metadata": {"role": "recon", "status": "idle"}}

    print("=== NACOS UI STATE DEMO ===")
    print(f"Nacos console: http://127.0.0.1:8848/nacos/")
    print("Login if prompted: nacos / nacos")
    print(f"Service name: {SERVICE_NAME}")

    try:
        for instance in [primary, backup]:
            registry.register_service(
                SERVICE_NAME,
                instance["ip"],
                instance["port"],
                metadata={
                    **instance["metadata"],
                    "demo": "nacos_ui_states",
                    "agent_name": "Recon_Primary" if instance is primary else "Recon_Backup",
                },
                heartbeat_interval=1,
                ephemeral=True,
            )
        hold("PHASE 1: both Agents are registered and idle", args.hold_seconds)

        registry.update_instance_metadata(
            SERVICE_NAME,
            primary,
            metadata_updates={
                "status": "busy",
                "lease_workflow_id": "ui-demo-workflow",
                "lease_work_item": "ui-demo-workflow:recon",
            },
        )
        hold("PHASE 2: Primary is busy with an active lease", args.hold_seconds)

        registry.update_instance_metadata(
            SERVICE_NAME,
            primary,
            metadata_updates={
                "status": "unavailable",
                "unavailable_reason": "simulated heartbeat lost",
                "unavailable_error_code": "AGENT_HEARTBEAT_LOST",
                "circuit_state": "open",
            },
        )
        registry.update_instance_metadata(
            SERVICE_NAME,
            backup,
            metadata_updates={
                "status": "busy",
                "lease_workflow_id": "ui-demo-workflow",
                "lease_work_item": "ui-demo-workflow:recon-reassigned",
            },
        )
        hold("PHASE 3: Primary unavailable/circuit open, Backup busy after reassignment", args.hold_seconds)

        registry.update_instance_metadata(
            SERVICE_NAME,
            primary,
            metadata_updates={"status": "idle", "circuit_state": "closed"},
            remove_keys=[
                "lease_workflow_id",
                "lease_work_item",
                "unavailable_reason",
                "unavailable_error_code",
            ],
        )
        registry.update_instance_metadata(
            SERVICE_NAME,
            backup,
            metadata_updates={"status": "idle"},
            remove_keys=["lease_workflow_id", "lease_work_item"],
        )
        hold("PHASE 4: both Agents recovered to idle", args.hold_seconds)
    finally:
        registry.close()
        if args.keep:
            print("\n[KEEP] Demo instances left in Nacos.")
        else:
            delete_instance(args.nacos_addr, ip, 19101)
            delete_instance(args.nacos_addr, ip, 19102)
            print("\n[CLEANUP] Demo instances deleted from Nacos.")


if __name__ == "__main__":
    main()
