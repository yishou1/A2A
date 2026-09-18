from __future__ import annotations

import os

from a2a_sdk import AgentRuntimeSDK
from cooperative_execution_agent.agent import CooperativeExecutionAgent


def required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def build_runtime() -> AgentRuntimeSDK:
    agent_id = required_env("COOP_EXECUTION_AGENT_ID")
    port = int(required_env("COOP_EXECUTION_AGENT_PORT"))
    state_db = os.environ.get(
        "COOP_EXECUTION_STATE_DB",
        os.path.join(".a2a_state", f"{agent_id}.execution.sqlite"),
    )
    agent = CooperativeExecutionAgent(
        agent_id=agent_id,
        port=port,
        state_db=state_db,
        resource_types=required_env("COOP_EXECUTION_RESOURCE_TYPES"),
        capabilities=required_env("COOP_EXECUTION_CAPABILITIES"),
        allowed_event_sources=os.environ.get("COOP_EXECUTION_EVENT_SOURCES", ""),
        max_concurrent_tasks=int(
            os.environ.get("COOP_EXECUTION_MAX_CONCURRENT_TASKS", "1")
        ),
    )
    return AgentRuntimeSDK.from_agent(
        agent,
        heartbeat_interval=float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5")),
        extra_metadata={
            "agent_id": agent_id,
            "capability": "cooperative_execution",
            "resource_types": ",".join(agent.resource_types),
            "execution_capabilities": ",".join(agent.execution_capabilities),
            "actuator_connected": "false",
        },
    )


def main() -> None:
    runtime = build_runtime()
    try:
        runtime.serve(ip=os.environ.get("COOP_EXECUTION_AGENT_HOST") or None)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
