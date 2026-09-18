from __future__ import annotations

import asyncio
import json
import os

from a2a_protocol.server import A2ABaseAgent
from a2a_sdk import AgentRuntimeSDK
from services.a2a_algorithms_common.rule_based_execution import (
    simulate_rule_based_execution,
)


def _profile(payload: dict) -> str:
    input_data = payload.get("input") if isinstance(payload.get("input"), dict) else {}
    return str(
        input_data.get("simulation_profile")
        or os.environ.get("RULE_SIMULATION_PROFILE")
        or "medium"
    )


def execute_artillery_command(payload: dict) -> tuple[dict, str]:
    result = simulate_rule_based_execution(
        payload,
        action_kind="artillery",
        profile_name=_profile(payload),
        profile_path=os.environ.get("RULE_SIMULATION_PROFILE_PATH"),
    )
    output = result["output_data"]
    message = (
        f"Rule simulation {output['decision']} for "
        f"target={output.get('target_id')} command={output.get('command_id')}"
    )
    return result, message


class ArtilleryAgent(A2ABaseAgent):
    def __init__(self, port: int, **kwargs):
        super().__init__(
            name="Rule_Based_Artillery_Simulation_Agent",
            description="Non-actuating deterministic strike simulation from approved upstream data.",
            role="artillery",
            port=port,
            **kwargs,
        )

    def execute_task(self, payload: dict):
        structured, message = execute_artillery_command(payload)
        output_hint = payload.get("output_hint") or "strike_result"
        return {
            output_hint: structured,
            "structured_strike_result": structured,
        }, message

    async def execute_stream(self, payload: dict):
        yield "data: " + json.dumps(
            {
                "status": "Working",
                "progress": "25%",
                "message": "validating authorization and execution inputs",
            }
        ) + "\n\n"
        await asyncio.sleep(0)
        output, message = await asyncio.to_thread(self.execute_task, payload)
        result = next(iter(output.values()))
        yield "data: " + json.dumps(
            {
                "status": "Completed",
                "progress": "100%",
                "message": message,
                "assessment_status": result["output_data"]["assessment_status"],
                "output": output,
            }
        ) + "\n\n"


def main() -> None:
    port = int(os.environ.get("ARTILLERY_AGENT_PORT", "8003"))
    agent = ArtilleryAgent(port=port)
    runtime = AgentRuntimeSDK.from_agent(
        agent,
        heartbeat_interval=float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5")),
        extra_metadata={
            "capability": "rule_based_strike_simulation",
            "execution_mode": "rule_based_simulation",
            "actuator_connected": "false",
        },
    )
    try:
        runtime.serve()
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
