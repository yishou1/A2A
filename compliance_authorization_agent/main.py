import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decision_agents.common.a2a_adapter import DecisionAlgorithmA2AAgent
from decision_agents.compliance_authorization.agent import ComplianceAuthorizationAgent
from registry.nacos_manager import NacosRegistry, get_host_ip


if __name__ == "__main__":
    port = int(os.environ.get("COMPLIANCE_AUTHORIZATION_AGENT_PORT", "10203"))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    agent = DecisionAlgorithmA2AAgent(
        algorithm_agent=ComplianceAuthorizationAgent(),
        name="Compliance_Authorization_Agent",
        description="Checks rules, law-of-war constraints, and authorization status.",
        role="compliance_authorization",
        port=port,
    )

    registry = NacosRegistry()
    registry.register_service(
        service_name="A2A-Agent",
        ip=get_host_ip(),
        port=port,
        metadata={
            "role": "compliance_authorization",
            "status": "idle",
            "capability": "compliance_authorization",
        },
        heartbeat_interval=heartbeat_interval,
    )
    agent.start()
