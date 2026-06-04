import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from a2a_protocol.server import A2ABaseAgent
from registry.nacos_manager import NacosRegistry, get_host_ip
from closed_loop_agent.closed_loop_core import _closed_loop_optimization


class ClosedLoopAgent(A2ABaseAgent):
    def __init__(self, port: int):
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
                    "name": "closed_loop_optimization",
                    "description": "执行控制、效果评估与闭环优化",
                    "input": {
                        "targets": "Optional live target list. If omitted, simulated targets are generated.",
                        "dataset_paths.xbd_damage_csv": "Optional xBD feature table for damage model training.",
                        "dataset_paths.sc2le_task_csv": "Optional SC2LE task feature table for mission model training.",
                        "cycles": "Closed-loop iteration count, 1 to 8.",
                    },
                    "output": {
                        "execution_control": "Final control commands per target.",
                        "effect_assessment": "Damage probabilities and situation labels.",
                        "closed_loop_optimization": "Cycle history and mission completion improvement.",
                        "requirement_report": "Requirement check result.",
                    },
                }
            ],
        )

    def _build_closed_loop_arguments(self, payload: dict) -> dict:
        arguments = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        for passthrough_key in ("targets", "results", "previous_results", "dataset_paths", "cycles", "seed", "target_count"):
            if passthrough_key in payload and passthrough_key not in arguments:
                arguments[passthrough_key] = payload[passthrough_key]
        return arguments

    def execute_task(self, payload: dict):
        command = payload.get("command") or "closed_loop_optimization"
        if command != "closed_loop_optimization":
            raise ValueError(f"Unsupported command: {command}")

        result = _closed_loop_optimization(self._build_closed_loop_arguments(payload))
        output_hint = payload.get("output_hint") or "closed_loop_result"
        output_data = result.get("output_data", {}) if isinstance(result, dict) else {}
        meets_requirements = output_data.get("meets_requirements")
        message = (
            "Closed loop optimization completed"
            if meets_requirements is not False
            else "Closed loop optimization completed with unmet requirements"
        )
        return {output_hint: result}, message

    async def handle_message(self, payload: dict):
        output, message = self.execute_task(payload)
        return {
            "task_id": self._work_item_from_payload(payload),
            "status": "Completed",
            "role": self.role,
            "command": payload.get("command") or "closed_loop_optimization",
            "result": next(iter(output.values()), None),
            "message": message,
        }

    async def execute_stream(self, payload):
        yield f"data: {json.dumps({'status': 'Working', 'progress': '10%', 'message': 'closed loop optimization started'}, ensure_ascii=False)}\n\n"
        output, message = self.execute_task(payload)
        result = next(iter(output.values()), {})
        summary = {
            "status": "Completed",
            "progress": "100%",
            "message": message,
            "meets_requirements": result.get("output_data", {}).get("meets_requirements") if isinstance(result, dict) else None,
        }
        yield f"data: {json.dumps(summary, ensure_ascii=False)}\n\n"


if __name__ == "__main__":
    port = int(os.environ.get("CLOSED_LOOP_AGENT_PORT", "8016"))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    agent = ClosedLoopAgent(port=port)

    registry = NacosRegistry()
    ip = get_host_ip()
    registry.register_service(
        service_name="A2A-Agent",
        ip=ip,
        port=port,
        metadata={"role": "closed_loop", "status": "idle"},
        heartbeat_interval=heartbeat_interval,
    )
    agent.start()
