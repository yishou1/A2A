import asyncio
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from a2a_protocol.server import A2ABaseAgent
from a2a_sdk import AgentRuntimeSDK
from execution_control_agent.algolib_runtime import run_execution_control_with_backend

EXECUTION_CONTROL_COMMAND = "generate_execution_commands"
PASSTHROUGH_INPUT_KEYS = (
    "phase",
    "control_phase",
    "results",
    "context",
    "targets",
)


def build_execution_control_arguments(payload: dict) -> dict:
    arguments = dict(payload.get("input")) if isinstance(payload.get("input"), dict) else {}
    for key in PASSTHROUGH_INPUT_KEYS:
        if key in payload and key not in arguments:
            arguments[key] = payload[key]
    if "context" not in arguments and isinstance(payload.get("context"), dict):
        arguments["context"] = payload["context"]
    if "results" not in arguments and isinstance(payload.get("results"), dict):
        arguments["results"] = payload["results"]
    return arguments


class ExecutionControlAgent(A2ABaseAgent):
    def __init__(self, port: int, **kwargs):
        super().__init__(
            name="Execution_Control_Agent",
            description="Generates executable strike/assault commands from upstream agent results.",
            role="execution_control",
            port=port,
            skills=[
                {
                    "id": EXECUTION_CONTROL_COMMAND,
                    "name": "Execution Control Planning",
                    "description": "关联规则匹配 + 线性回归运动预测，生成结构化作战指令",
                    "tags": ["execution_control", "planning", "command", "执行控制"],
                    "input": {
                        "phase": "strike or assault",
                        "results": "Upstream standardized agent outputs",
                    },
                    "output": {
                        "execution_control_result": "Structured commands/tracks/coordination envelope",
                    },
                },
                {
                    "id": "plan_strike_control",
                    "name": "Strike Execution Control",
                    "description": "火力压制阶段：生成 strike 可执行指令",
                    "tags": ["execution_control", "strike", "planning", "火力控制"],
                    "input": {
                        "results": "Upstream standardized agent outputs",
                    },
                    "output": {
                        "execution_control_result": "Strike-phase commands/tracks/coordination envelope",
                    },
                },
                {
                    "id": "plan_assault_control",
                    "name": "Assault Execution Control",
                    "description": "突击阶段：生成 assault 可执行指令",
                    "tags": ["execution_control", "assault", "planning", "突击控制"],
                    "input": {
                        "results": "Upstream standardized agent outputs",
                    },
                    "output": {
                        "execution_control_result": "Assault-phase commands/tracks/coordination envelope",
                    },
                },
            ],
            **kwargs,
        )

    def execute_task(self, payload: dict):
        command = payload.get("command") or EXECUTION_CONTROL_COMMAND
        if command not in {EXECUTION_CONTROL_COMMAND, "plan_strike_control", "plan_assault_control"}:
            raise ValueError(f"Unsupported command: {command}")
        if command == "plan_strike_control":
            payload = dict(payload)
            input_data = dict(payload.get("input") or {})
            input_data["phase"] = "strike"
            payload["input"] = input_data
        elif command == "plan_assault_control":
            payload = dict(payload)
            input_data = dict(payload.get("input") or {})
            input_data["phase"] = "assault"
            payload["input"] = input_data

        result = run_execution_control_with_backend(build_execution_control_arguments(payload))
        output_hint = payload.get("output_hint") or "execution_control_result"
        return {output_hint: result}, "Execution control commands generated"

    async def execute_stream(self, payload):
        yield (
            "data: "
            + json.dumps(
                {"status": "Working", "progress": "10%", "message": "execution control started"},
                ensure_ascii=False,
            )
            + "\n\n"
        )
        output, message = await asyncio.to_thread(self.execute_task, payload)
        summary = {
            "status": "Completed",
            "progress": "100%",
            "message": message,
            "command_count": len(next(iter(output.values()), {}).get("output_data", {}).get("commands", [])),
            "backend": next(iter(output.values()), {}).get("output_data", {}).get("backend"),
        }
        yield "data: " + json.dumps(summary, ensure_ascii=False) + "\n\n"


if __name__ == "__main__":
    port = int(os.environ.get("EXECUTION_CONTROL_AGENT_PORT", "8017"))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    agent = ExecutionControlAgent(port=port)
    runtime = AgentRuntimeSDK.from_agent(
        agent,
        heartbeat_interval=heartbeat_interval,
        extra_metadata={"capability": "execution_control"},
    )
    try:
        runtime.serve()
    finally:
        runtime.close()
