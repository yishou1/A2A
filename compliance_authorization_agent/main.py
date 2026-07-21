import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from a2a_sdk import AgentRuntimeSDK
from decision_agents.common.a2a_adapter import DecisionAlgorithmA2AAgent
from decision_agents.compliance_authorization.agent import ComplianceAuthorizationAgent
from decision_agents.common.definitions import agent_definition


if __name__ == "__main__":
    definition = agent_definition("compliance_authorization")
    port = int(
        os.environ.get(
            "COMPLIANCE_AUTHORIZATION_AGENT_PORT",
            str(definition["default_port"]),
        )
    )
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    agent = DecisionAlgorithmA2AAgent(
        algorithm_agent=ComplianceAuthorizationAgent(),
        name=definition["runtime_name"],
        description=definition["description"],
        role=definition["role"],
        port=port,
    )
    runtime = AgentRuntimeSDK.from_agent(
        agent,
        heartbeat_interval=heartbeat_interval,
        extra_metadata={"capability": definition["role"]},
    )
    try:
        runtime.serve()
    finally:
        runtime.close()
