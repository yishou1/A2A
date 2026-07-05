from a2a_protocol.server import A2ABaseAgent, skills_metadata
from registry.nacos_manager import NacosRegistry, get_host_ip
import asyncio
import json
import os

class ArtilleryAgent(A2ABaseAgent):
    async def execute_stream(self, payload):
        yield f"data: {json.dumps({'status': 'Working', 'message': 'Target locked', 'progress': '10%'})}\n\n"
        await asyncio.sleep(1)
        yield f"data: {json.dumps({'status': 'Working', 'message': 'Firing Volley 1', 'progress': '30%'})}\n\n"
        await asyncio.sleep(1)
        yield f"data: {json.dumps({'status': 'Working', 'message': 'Impact confirmed. Adjusting aim.', 'progress': '60%'})}\n\n"
        await asyncio.sleep(1)
        yield f"data: {json.dumps({'status': 'Completed', 'message': 'Target suppression complete', 'progress': '100%'})}\n\n"

if __name__ == "__main__":
    port = int(os.environ.get("ARTILLERY_AGENT_PORT", "8003"))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    agent = ArtilleryAgent(

        name="Artillery_Agent",
        description="Assigned heavy artillery forces for beach suppression.",
        role="artillery",
        port=port
    )
    
    registry = NacosRegistry()
    ip = get_host_ip()
    
    # Run server in main thread, registration before
    registry.register_service(
        service_name="A2A-Agent",
        ip=ip,
        port=port,
        metadata={
            "role": "artillery",
            "firepower": "heavy",
            "status": "idle",
            **skills_metadata(agent.skills),
            **agent.heartbeat_metadata(),
        },
        heartbeat_interval=heartbeat_interval,
        metadata_provider=agent.heartbeat_metadata,
    )
    agent.start()
