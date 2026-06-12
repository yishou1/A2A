"""战术情报 Agent 的 Commander 协议实现（兼容 TIA 独立仓库与 A2A-main）。"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any, AsyncIterator

try:
    from a2a_protocol.server import A2ABaseAgent as _ServerBase
except ImportError:  # TIA 独立仓库无 server.A2ABaseAgent
    from a2a_protocol.commander_server import A2ABaseAgent as _ServerBase

from agent.models.schemas import SemanticIntelligencePacket
from agent.orchestrator import TacticalIntelligenceAgent
from tactical_intelligence_agent.bootstrap import create_engine, default_role, warmup_inference
from tactical_intelligence_agent.payload_adapter import commander_payload_to_batch

AGENT_NAME = "Tactical_Intelligence_Agent"
AGENT_DESCRIPTION = (
    "Single tactical intelligence agent: perception → cognition → communication pipeline"
)


class _CommanderAgentBase(_ServerBase):
    """
    扩展 A2A-main A2ABaseAgent：支持 build_send_message_response 与返回 role 字段。
    与 recon/artillery 同级，可直接被 Commander A2AClient 调度。
    """

    def build_send_message_response(self, payload: dict, work_item: str) -> dict:
        return {
            "work_item": work_item,
            "workflow_id": payload.get("workflow_id"),
            "status": "Accepted",
            "role": self.role,
            "message": f"{self.name} received work item {payload.get('command')}",
            "work_list_size": len(self.get_work_list(payload.get("workflow_id"))),
        }

    def setup_routes(self, app=None):
        from fastapi import Depends
        from fastapi.responses import StreamingResponse

        try:
            from a2a_protocol.server import verify_token
        except ImportError:
            from a2a_protocol.commander_server import verify_token

        target = app if app is not None else self.app

        @target.get("/.well-known/agent-card")
        async def agent_card():
            return self.get_agent_card()

        @target.get("/workflows/{workflow_id}/work-list")
        async def workflow_work_list(workflow_id: str):
            return {
                "workflow_id": workflow_id,
                "agent": self.name,
                "role": self.role,
                "work_list": self.get_work_list(workflow_id),
            }

        @target.post("/sendMessage")
        async def send_message(payload: dict, token: str = Depends(verify_token)):
            self._capture_work_list(payload)
            work_item = self._work_item_from_payload(payload)
            with self._state_lock:
                cached_response = self._task_response_cache.get(work_item)
            if cached_response is not None:
                return cached_response

            response = self.build_send_message_response(payload, work_item)
            with self._state_lock:
                self._task_response_cache[work_item] = response
            return response

        @target.post("/sendMessageStream")
        async def send_message_stream(payload: dict, token: str = Depends(verify_token)):
            return StreamingResponse(
                self._cached_stream(payload),
                media_type="text/event-stream",
            )


class TacticalIntelligenceCommanderAgent(_CommanderAgentBase):
    """
    单体战术情报 Agent，对外暴露 A2A-main 标准 sendMessage / sendMessageStream。

    进程内串联感知、认知、通信三技能；同一 work_item 幂等缓存由 A2ABaseAgent 提供。
    """

    def __init__(
        self,
        *,
        port: int,
        engine: TacticalIntelligenceAgent | None = None,
        role: str | None = None,
    ):
        self._engine = engine or create_engine()
        self._result_cache: dict[str, SemanticIntelligencePacket] = {}
        self._result_lock = threading.RLock()
        if engine is None and os.environ.get("TIA_SKIP_WARMUP", "0") != "1":
            warmup_inference()
        super().__init__(
            name=AGENT_NAME,
            description=AGENT_DESCRIPTION,
            role=role or default_role(),
            port=port,
        )

    def get_agent_card(self):
        card = super().get_agent_card()
        card["capabilities"] = [
            "semantic_intelligence",
            "multimodal_fusion",
            "anti_jam_routing",
        ]
        return card

    def _get_or_process(self, payload: dict) -> SemanticIntelligencePacket:
        work_item = self._work_item_from_payload(payload)
        with self._result_lock:
            cached = self._result_cache.get(work_item)
        if cached is not None:
            return cached

        batch = commander_payload_to_batch(payload)
        packet = self._engine.process(batch)
        with self._result_lock:
            self._result_cache[work_item] = packet
        return packet

    def build_send_message_response(self, payload: dict, work_item: str) -> dict:
        try:
            packet = self._get_or_process(payload)
            return {
                "work_item": work_item,
                "workflow_id": payload.get("workflow_id"),
                "status": "Accepted",
                "role": self.role,
                "message": (
                    f"Tactical intelligence completed command={payload.get('command')}; "
                    f"targets={len(packet.targets)}; summary={packet.summary[:120]}"
                ),
                "intelligence_packet_id": packet.packet_id,
                "target_count": len(packet.targets),
                "work_list_size": len(self.get_work_list(payload.get("workflow_id"))),
            }
        except Exception as exc:
            return {
                "work_item": work_item,
                "workflow_id": payload.get("workflow_id"),
                "status": "Failed",
                "role": self.role,
                "message": f"Tactical intelligence failed: {exc}",
                "work_list_size": len(self.get_work_list(payload.get("workflow_id"))),
            }

    def _sse_event(self, **fields: Any) -> str:
        return f"data: {json.dumps(fields, ensure_ascii=False)}\n\n"

    async def execute_stream(self, payload: dict) -> AsyncIterator[str]:
        work_item = self._work_item_from_payload(payload)
        command = payload.get("command") or "process_intelligence"

        yield self._sse_event(
            status="Working",
            progress="10%",
            stage="perception",
            message="Perception: RT-DETR / Siamese-Mask2Former / MOTR+Kalman",
            work_item=work_item,
            role=self.role,
        )
        await asyncio.sleep(0.05)

        yield self._sse_event(
            status="Working",
            progress="45%",
            stage="cognition",
            message="Cognition: ImageBind / Mamba / SynapseRAG fusion",
            work_item=work_item,
            role=self.role,
        )
        await asyncio.sleep(0.05)

        yield self._sse_event(
            status="Working",
            progress="75%",
            stage="communication",
            message="Communication: Knowledge Semantic Comm / MARL routing",
            work_item=work_item,
            role=self.role,
        )

        try:
            packet = await asyncio.to_thread(self._get_or_process, payload)
        except Exception as exc:
            yield self._sse_event(
                status="Failed",
                progress="100%",
                stage="error",
                role=self.role,
                work_item=work_item,
                message=f"Tactical intelligence failed: {exc}",
            )
            return

        yield self._sse_event(
            status="Completed",
            progress="100%",
            stage="done",
            role=self.role,
            work_item=work_item,
            message=(
                f"Tactical intelligence completed command={command}; "
                f"targets={len(packet.targets)}"
            ),
            intelligence_packet=packet.model_dump(mode="json"),
        )
