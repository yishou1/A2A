"""Start configured cooperative execution Agent processes.

The launcher requires an explicit JSON configuration. It does not invent
platform identities, capabilities, resource values or external evidence.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


REQUIRED_FIELDS = {"agent_id", "port", "resource_types", "capabilities"}
COMMANDER_ROOT = Path(__file__).resolve().parents[1]


def load_config(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    agents = payload.get("agents") if isinstance(payload, dict) else None
    if not isinstance(agents, list) or not agents:
        raise ValueError("configuration must contain a non-empty agents list")
    ids = set()
    ports = set()
    for index, row in enumerate(agents):
        if not isinstance(row, dict):
            raise ValueError(f"agents[{index}] must be an object")
        missing = sorted(REQUIRED_FIELDS - set(row))
        if missing:
            raise ValueError(f"agents[{index}] missing fields: {', '.join(missing)}")
        agent_id = str(row["agent_id"]).strip()
        port = int(row["port"])
        if not agent_id or agent_id in ids:
            raise ValueError(f"duplicate or empty agent_id: {agent_id}")
        if port in ports:
            raise ValueError(f"duplicate port: {port}")
        ids.add(agent_id)
        ports.add(port)
    return agents


def environment(row: dict, config_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    state_db = row.get("state_db") or str(
        config_dir / ".a2a_state" / f"{row['agent_id']}.execution.sqlite"
    )
    env.update(
        {
            "COOP_EXECUTION_AGENT_ID": str(row["agent_id"]),
            "COOP_EXECUTION_AGENT_PORT": str(int(row["port"])),
            "COOP_EXECUTION_AGENT_HOST": str(row.get("host") or ""),
            "COOP_EXECUTION_RESOURCE_TYPES": ",".join(row["resource_types"]),
            "COOP_EXECUTION_CAPABILITIES": ",".join(row["capabilities"]),
            "COOP_EXECUTION_EVENT_SOURCES": ",".join(
                row.get("allowed_event_sources") or []
            ),
            "COOP_EXECUTION_MAX_CONCURRENT_TASKS": str(
                int(row.get("max_concurrent_tasks", 1))
            ),
            "COOP_EXECUTION_STATE_DB": str(state_db),
        }
    )
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{COMMANDER_ROOT}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(COMMANDER_ROOT)
    )
    return env


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    config_path = args.config.resolve()
    agents = load_config(config_path)
    print(f"validated {len(agents)} cooperative execution Agents")
    if args.check:
        return 0
    processes = []
    for row in agents:
        process = subprocess.Popen(
            [sys.executable, "-m", "cooperative_execution_agent.main"],
            env=environment(row, config_path.parent),
            cwd=COMMANDER_ROOT,
        )
        processes.append((str(row["agent_id"]), process))
        print(f"started {row['agent_id']} pid={process.pid} port={row['port']}")
    try:
        return max(process.wait() for _, process in processes)
    except KeyboardInterrupt:
        for _, process in processes:
            process.terminate()
        for _, process in processes:
            process.wait(timeout=10)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
