from a2a_protocol.server import A2ABaseAgent, skills_metadata
from registry.nacos_manager import NacosRegistry, get_host_ip
from model_registry import build_model
import os

if __name__ == "__main__":
    port = int(os.environ.get("EVALUATOR_AGENT_PORT", "8005"))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    agent = A2ABaseAgent(
        name="Evaluator_Agent",
        description="Evaluates the battle outcome to trigger replanning.",
        role="evaluator",
        port=port,
        models=[
            build_model(
                "threat_eval_v1",
                name="Threat & Strike Evaluation Model",
                model_type="evaluation",
                tags=["threat_evaluation", "strike_effect_evaluation"],
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
            "role": "evaluator",
            "status": "idle",
            **skills_metadata(agent.skills),
            **agent.heartbeat_metadata(),
        },
        heartbeat_interval=heartbeat_interval,
        metadata_provider=agent.heartbeat_metadata,
    )
    agent.start()
