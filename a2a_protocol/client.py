import requests
import sseclient
from pydantic import BaseModel
from typing import Dict, Any, Optional

class A2AClient:
    def __init__(self, target_ip, target_port):
        self.base_url = f"http://{target_ip}:{target_port}"
        self.agent_card = None
        self.jwt_token = None
        self.http = requests.Session()
        self.http.trust_env = False
    
    def discover(self):
        """1. Agent Discovery: GET /.well-known/agent-card"""
        url = f"{self.base_url}/.well-known/agent-card"
        res = self.http.get(url, timeout=5)
        if res.status_code == 200:
            self.agent_card = res.json()
            return self.agent_card
        raise Exception(f"Failed to fetch Agent Card from {url}")

    def authenticate(self, client_id="commander", client_secret="secret"):
        """2. Authentication: Parse Agent Card and request JWT"""
        if not self.agent_card:
            self.discover()
            
        schemes = self.agent_card.get("securitySchemes", {})
        oidc = schemes.get("openIdConnect")
        if oidc:
            token_url = oidc.get("tokenUrl")
            # Mock JWT request to Auth Server
            # In a real setup, we use actual OAuth2 Client Credentials flow
            # Using httpbin mock here:
            auth_res = self.http.post(token_url, json={"client_id": client_id}, timeout=5)
            # Generate a fake JWT for simulation
            self.jwt_token = "mock-jwt-token-abcd"
            return self.jwt_token
        raise Exception("openIdConnect not found in agent card")

    def send_message(self, task_payload: Dict[str, Any]):
        """3. sendMessage API"""
        if not self.jwt_token:
            self.authenticate()
            
        url = f'{self.base_url}{self.agent_card.get("sendMessageEndpoint", "/sendMessage")}'
        headers = {"Authorization": f"Bearer {self.jwt_token}"}
        res = self.http.post(url, json=task_payload, headers=headers, timeout=5)
        return res.json()

    def send_message_stream(self, task_payload: Dict[str, Any]):
        """4. sendMessageStream API using SSE"""
        if not self.jwt_token:
            self.authenticate()
            
        url = f'{self.base_url}{self.agent_card.get("sendMessageStreamEndpoint", "/sendMessageStream")}'
        headers = {
            "Authorization": f"Bearer {self.jwt_token}",
            "Accept": "text/event-stream"
        }
        res = self.http.post(url, json=task_payload, headers=headers, stream=True, timeout=30)
        # Process SSE
        client = sseclient.SSEClient(res)
        for event in client.events():
            if event.data:
                yield event.data
