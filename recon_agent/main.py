from a2a_protocol.server import A2ABaseAgent, skills_metadata
from registry.nacos_manager import NacosRegistry, get_host_ip
from model_registry import build_model
import os

if __name__ == "__main__":
    port = int(os.environ.get("RECON_AGENT_PORT", "8002"))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    agent = A2ABaseAgent(
        name="Recon_Agent",
        description="Performs reconnaissance to gather enemy positions and weather.",
        role="recon",
        port=port,
        models=[
            build_model(
                "recon_detector_v1",
                name="Recon Detection Model",
                model_type="detection",
                tags=["detect", "locate", "identify"],
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
            "role": "recon",
            "status": "idle",
            **skills_metadata(agent.skills),
            **agent.heartbeat_metadata(),
        },
        heartbeat_interval=heartbeat_interval,
        metadata_provider=agent.heartbeat_metadata,
    )
    agent.start()
