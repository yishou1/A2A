from a2a_protocol.server import A2ABaseAgent, skills_metadata
from registry.nacos_manager import NacosRegistry, get_host_ip
from model_registry import build_model
import os
# 111
if __name__ == "__main__":
    port = int(os.environ.get("ASSAULT_AGENT_PORT", "8004"))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    agent = A2ABaseAgent(
        name="Assault_Agent",
        description="Assault infantry unit for capturing the beachhead.",
        role="assault",
        port=port,
        models=[
            build_model(
                "route_planner_v1",
                name="Assault Route Planning Model",
                model_type="route_planning",
                tags=["route_planning", "target_assignment"],
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
            "role": "assault",
            "status": "idle",
            **skills_metadata(agent.skills),
            **agent.heartbeat_metadata(),
        },
        heartbeat_interval=heartbeat_interval,
        metadata_provider=agent.heartbeat_metadata,
    )
    agent.start()
