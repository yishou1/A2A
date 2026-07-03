from a2a_protocol.server import A2ABaseAgent
from registry.nacos_manager import NacosRegistry, get_host_ip
import os
import time


def execute_assault_command(payload: dict) -> tuple[dict, str]:
    input_data = payload.get("input") if isinstance(payload.get("input"), dict) else {}
    execution_command = input_data.get("execution_command") if isinstance(input_data.get("execution_command"), dict) else {}
    action = execution_command.get("action") or payload.get("command") or "capture_beachhead"
    target_id = execution_command.get("target_id") or "unknown"
    aim_point = execution_command.get("aim_point") if isinstance(execution_command.get("aim_point"), dict) else {}
    command_id = execution_command.get("command_id")
    start = time.perf_counter()
    latency_ms = round((time.perf_counter() - start) * 1000.0, 3)
    message = (
        f"Assault action {action} against {target_id} "
        f"at ({aim_point.get('x', 'NA')}, {aim_point.get('y', 'NA')}) completed"
    )
    structured = {
        "task_type": "assault_execution",
        "input_data": input_data,
        "output_data": {
            "action": action,
            "target_id": target_id,
            "aim_point": aim_point,
            "command_id": command_id,
            "executor_role": "assault",
            "status": "completed",
            "message": message,
            "latency_ms": latency_ms,
        },
        "accuracy": 1.0,
        "latency": latency_ms / 1000.0,
    }
    return structured, message


class AssaultAgent(A2ABaseAgent):
    def __init__(self, port: int):
        super().__init__(
            name="Assault_Agent",
            description="Assault infantry unit for capturing the beachhead.",
            role="assault",
            port=port,
        )

    def execute_task(self, payload: dict):
        structured, message = execute_assault_command(payload)
        output_hint = payload.get("output_hint") or "assault_result"
        return {output_hint: structured, "structured_assault_result": structured}, message


if __name__ == "__main__":
    port = int(os.environ.get("ASSAULT_AGENT_PORT", "8004"))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    agent = AssaultAgent(port=port)
    
    registry = NacosRegistry()
    ip = get_host_ip()
    
    registry.register_service(
        service_name="A2A-Agent",
        ip=ip,
        port=port,
        metadata={"role": "assault", "status": "idle"},
        heartbeat_interval=heartbeat_interval,
    )
    agent.start()
