import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decision_agents.a2a_adapter import DecisionAlgorithmA2AAgent
from decision_agents.agents import TrackThreatAgent
from registry.nacos_manager import NacosRegistry, get_host_ip


if __name__ == "__main__":
    port = int(os.environ.get("TRACK_THREAT_AGENT_PORT", "10201"))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    agent = DecisionAlgorithmA2AAgent(
        algorithm_agent=TrackThreatAgent(),
        name="Track_Threat_Agent",
        description="Generates tracks and threat priority rankings.",
        role="track_threat",
        port=port,
    )

    registry = NacosRegistry()
    registry.register_service(
        service_name="A2A-Agent",
        ip=get_host_ip(),
        port=port,
        metadata={"role": "track_threat", "status": "idle", "capability": "track_threat"},
        heartbeat_interval=heartbeat_interval,
    )
    agent.start()
