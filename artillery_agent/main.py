from a2a_protocol.server import A2ABaseAgent, skills_metadata
from registry.nacos_manager import NacosRegistry, get_host_ip
from model_registry import build_model
import asyncio
import json
import os
import time


def execute_artillery_command(payload: dict) -> tuple[dict, str]:
    input_data = payload.get("input") if isinstance(payload.get("input"), dict) else {}
    execution_command = input_data.get("execution_command") if isinstance(input_data.get("execution_command"), dict) else {}
    action = execution_command.get("action") or payload.get("command") or "suppress_beach_sector_A"
    target_id = execution_command.get("target_id") or "unknown"
    aim_point = execution_command.get("aim_point") if isinstance(execution_command.get("aim_point"), dict) else {}
    command_id = execution_command.get("command_id")
    start = time.perf_counter()
    latency_ms = round((time.perf_counter() - start) * 1000.0, 3)
    message = (
        f"Executed {action} on {target_id} "
        f"at ({aim_point.get('x', 'NA')}, {aim_point.get('y', 'NA')})"
    )
    structured = {
        "task_type": "artillery_strike",
        "input_data": input_data,
        "output_data": {
            "action": action,
            "target_id": target_id,
            "aim_point": aim_point,
            "command_id": command_id,
            "executor_role": "artillery",
            "status": "completed",
            "message": message,
            "latency_ms": latency_ms,
        },
        "accuracy": 1.0,
        "latency": latency_ms / 1000.0,
    }
    return structured, message


class ArtilleryAgent(A2ABaseAgent):
    def __init__(self, port: int, models=None):
        super().__init__(
            name="Artillery_Agent",
            description="Assigned heavy artillery forces for beach suppression.",
            role="artillery",
            port=port,
            models=models,
        )

    def execute_task(self, payload: dict):
        structured, message = execute_artillery_command(payload)
        output_hint = payload.get("output_hint") or "strike_result"
        return {output_hint: structured, "structured_strike_result": structured}, message

    async def execute_stream(self, payload):
        yield f"data: {json.dumps({'status': 'Working', 'message': 'Target locked', 'progress': '10%'})}\n\n"
        await asyncio.sleep(0.2)
        yield f"data: {json.dumps({'status': 'Working', 'message': 'Firing Volley 1', 'progress': '30%'})}\n\n"
        await asyncio.sleep(0.2)
        yield f"data: {json.dumps({'status': 'Working', 'message': 'Impact confirmed. Adjusting aim.', 'progress': '60%'})}\n\n"
        await asyncio.sleep(0.2)
        output, message = await asyncio.to_thread(self.execute_task, payload)
        yield f"data: {json.dumps({'status': 'Completed', 'message': message, 'progress': '100%', 'output': output})}\n\n"


if __name__ == "__main__":
    port = int(os.environ.get("ARTILLERY_AGENT_PORT", "8003"))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    agent = ArtilleryAgent(
        port=port,
        models=[
            build_model(
                "fire_control_v1",
                name="Fire Control Model",
                model_type="target_assignment",
                tags=["target_assignment", "route_planning"],
            ),
        ],
    )
    
    registry = NacosRegistry()
    ip = get_host_ip()
    
    registry.register_service(
        service_name="A2A-Agent",
        ip=ip,
        port=port,
        metadata={
            "role": "artillery",
            "firepower": "heavy",
            "status": "idle",
            **skills_metadata(agent.skills),
            **agent.heartbeat_metadata(),
        },
        heartbeat_interval=heartbeat_interval,
        metadata_provider=agent.heartbeat_metadata,
    )
    agent.start()
