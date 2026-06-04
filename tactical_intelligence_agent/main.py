"""A2A-main 风格独立启动入口（合并到主项目时保留此文件）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from registry.nacos_manager import NacosRegistry, get_host_ip
from tactical_intelligence_agent.service import TacticalIntelligenceCommanderAgent

if __name__ == "__main__":
    port = int(os.environ.get("TIA_PORT", os.environ.get("TACTICAL_INTELLIGENCE_AGENT_PORT", "8015")))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    role = os.environ.get("TIA_A2A_ROLE", "tactical_intelligence")

    agent = TacticalIntelligenceCommanderAgent(port=port, role=role)

    if os.environ.get("TIA_NACOS_REGISTER", "1") == "1":
        registry = NacosRegistry()
        ip = get_host_ip()
        registry.register_service(
            service_name=os.environ.get("TIA_NACOS_SERVICE", "A2A-Agent"),
            ip=ip,
            port=port,
            metadata={
                "role": role,
                "status": "idle",
                "capability": "semantic_intelligence",
                "protocol": "http+a2a-commander",
            },
            heartbeat_interval=heartbeat_interval,
        )
        print(f"[NACOS] registered A2A-Agent at {ip}:{port} role={role}")

    agent.start()
