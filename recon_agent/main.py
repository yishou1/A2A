from a2a_protocol.server import A2ABaseAgent
from a2a_sdk import AgentRuntimeSDK
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
    runtime = AgentRuntimeSDK.from_agent(
        agent,
        heartbeat_interval=heartbeat_interval,
        extra_metadata={"capability": "recon"},
    )
    try:
        runtime.serve()
    finally:
        runtime.close()
