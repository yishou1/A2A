import asyncio
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from a2a_protocol.server import A2ABaseAgent
from a2a_sdk import AgentRuntimeSDK
from closed_loop_agent.algolib_runtime import run_closed_loop_with_backend

CLOSED_LOOP_COMMAND = "closed_loop_optimization"
PASSTHROUGH_INPUT_KEYS = (
    "targets",
    "results",
    "previous_results",
    "dataset_paths",
    "cycles",
    "seed",
    "target_count",
    "damage_input_mode",
    "xbd_input_mode",
    "device",
    "feature_mode",
)



def build_closed_loop_arguments(payload: dict) -> dict:
    """Build closed-loop core arguments from a standard A2A task payload."""
    arguments = dict(payload.get("input")) if isinstance(payload.get("input"), dict) else {}
    for key in PASSTHROUGH_INPUT_KEYS:
        if key in payload and key not in arguments:
            arguments[key] = payload[key]
    return arguments


class ClosedLoopAgent(A2ABaseAgent):
    def __init__(self, port: int, **kwargs):
        super().__init__(
            name="Closed_Loop_Optimization_Agent",
            description=(
                "Runs execution control, effect assessment and closed-loop "
                "optimization using logistic regression, K-Means and random forest regression."
            ),
            role="closed_loop",
            port=port,
            skills=[
                {
                    "id": CLOSED_LOOP_COMMAND,
                    "name": "Closed Loop Optimization",
                    "description": "执行控制、效果评估与闭环优化",
                    "tags": ["closed_loop", "optimization", "闭环", "优化"],
                    "input": {
                        "targets": "Optional live target list. If omitted, simulated targets are generated.",
                        "dataset_paths.xbd_damage_csv": "Optional xBD feature table for damage model training.",
                        "dataset_paths.sc2le_task_csv": "Optional SC2LE task feature table for mission model training.",
                        "cycles": "Closed-loop iteration count, 1 to 8.",
                    },
                    "output": {
                        "closed_loop_result": "Structured closed-loop optimization result envelope.",
                    },
                }
            ],
            **kwargs,
        )

    def execute_task(self, payload: dict):
        command = payload.get("command") or CLOSED_LOOP_COMMAND
        if command != CLOSED_LOOP_COMMAND:
            raise ValueError(f"Unsupported command: {command}")

        result = run_closed_loop_with_backend(build_closed_loop_arguments(payload))
        output_hint = payload.get("output_hint") or "closed_loop_result"
        output_data = result.get("output_data", {}) if isinstance(result, dict) else {}
        meets_requirements = output_data.get("meets_requirements")
        message = (
            "Closed loop optimization completed"
            if meets_requirements is not False
            else "Closed loop optimization completed with unmet requirements"
        )
        return {output_hint: result}, message

    async def execute_stream(self, payload):
        yield (
            "data: "
            + json.dumps(
                {
                    "status": "Working",
                    "progress": "10%",
                    "message": "closed loop optimization started",
                },
                ensure_ascii=False,
            )
            + "\n\n"
        )
        output, message = await asyncio.to_thread(self.execute_task, payload)
        result = next(iter(output.values()), {})
        summary = {
            "status": "Completed",
            "progress": "100%",
            "message": message,
            "meets_requirements": result.get("output_data", {}).get("meets_requirements")
            if isinstance(result, dict)
            else None,
            "backend": result.get("output_data", {}).get("backend") if isinstance(result, dict) else None,
        }
        yield "data: " + json.dumps(summary, ensure_ascii=False) + "\n\n"


if __name__ == "__main__":
    port = int(os.environ.get("CLOSED_LOOP_AGENT_PORT", "8016"))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    agent = ClosedLoopAgent(port=port)
    runtime = AgentRuntimeSDK.from_agent(
        agent,
        heartbeat_interval=heartbeat_interval,
        extra_metadata={"capability": "closed_loop"},
    )
    try:
        runtime.serve()
    finally:
        runtime.close()
