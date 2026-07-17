import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decision_agents.a2a_adapter import DecisionAlgorithmA2AAgent
from decision_agents.agents import DecisionPlanningAgent
from registry.nacos_manager import NacosRegistry, get_host_ip


if __name__ == "__main__":
    port = int(os.environ.get("DECISION_PLANNING_AGENT_PORT", "10202"))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    agent = DecisionAlgorithmA2AAgent(
        algorithm_agent=DecisionPlanningAgent(),
        name="Decision_Planning_Agent",
        description="Generates and scores simulation-only decision-support plans.",
        role="decision_planning",
        port=port,
    )

    registry = NacosRegistry()
    registry.register_service(
        service_name="A2A-Agent",
        ip=get_host_ip(),
        port=port,
        metadata={
            "role": "decision_planning",
            "status": "idle",
            "capability": "decision_planning",
        },
        heartbeat_interval=heartbeat_interval,
    )
    agent.start()
