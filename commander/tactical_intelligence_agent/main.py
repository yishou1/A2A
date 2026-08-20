"""A2A 风格独立启动入口 — 对齐 lzh 分支 AgentRuntimeSDK + 心跳注册。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from a2a_sdk import AgentRuntimeSDK
from tactical_intelligence_agent.service import TacticalIntelligenceCommanderAgent

if __name__ == "__main__":
    port = int(os.environ.get("TIA_PORT", os.environ.get("TACTICAL_INTELLIGENCE_AGENT_PORT", "8016")))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    role = os.environ.get("TIA_A2A_ROLE", "tactical_intelligence")
    register = os.environ.get("TIA_NACOS_REGISTER", "1") == "1"
    service_name = os.environ.get("TIA_NACOS_SERVICE", "A2A-Agent")

    agent = TacticalIntelligenceCommanderAgent(port=port, role=role)
    runtime = AgentRuntimeSDK.from_agent(
        agent,
        service_name=service_name,
        heartbeat_interval=heartbeat_interval,
        extra_metadata={
            "capability": "semantic_intelligence",
            "protocol": "http+a2a-commander",
            "output_hint": "intelligence_packet",
            "algorithm_library": "predict",
        },
    )
    try:
        print(
            f"[TIA] starting role={role} port={port} "
            f"register={register} heartbeat={heartbeat_interval}s"
        )
        if register:
            runtime.serve()
        else:
            agent.start()
    finally:
        runtime.close()
