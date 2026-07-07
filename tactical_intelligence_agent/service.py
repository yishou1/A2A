"""战术情报 Agent — 对齐 A2ABaseAgent 统一任务响应与宕机恢复接入规范。"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any, AsyncIterator

from a2a_protocol.server import A2ABaseAgent

from agent.models.schemas import SemanticIntelligencePacket
from agent.orchestrator import TacticalIntelligenceAgent
from tactical_intelligence_agent.bootstrap import create_engine, default_role, warmup_inference
from tactical_intelligence_agent.payload_adapter import commander_payload_to_batch

AGENT_NAME = "Tactical_Intelligence_Agent"
AGENT_DESCRIPTION = (
    "Single tactical intelligence agent: perception → cognition → communication pipeline"
)
DEFAULT_OUTPUT_HINT = "intelligence_packet"


class TacticalIntelligenceCommanderAgent(A2ABaseAgent):
    """
    战术情报 Agent：继承 A2ABaseAgent，自动获得
    /health /ready /metrics /lifecycle/ready、统一任务响应信封、work_item 幂等。
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

    def _build_output(self, payload: dict, packet: SemanticIntelligencePacket) -> dict[str, Any]:
        output_hint = payload.get("output_hint") or DEFAULT_OUTPUT_HINT
        packet_json = packet.model_dump(mode="json")
        output: dict[str, Any] = {
            output_hint: packet_json,
            "target_count": len(packet.targets),
            "summary": packet.summary,
            "output_attachments": packet.output_attachments,
        }
        if output_hint != DEFAULT_OUTPUT_HINT:
            output[DEFAULT_OUTPUT_HINT] = packet_json
        return output

    def execute_task(self, payload: dict) -> tuple[dict[str, Any], str]:
        packet = self._get_or_process(payload)
        output = self._build_output(payload, packet)
        message = (
            f"Tactical intelligence completed command={payload.get('command')}; "
            f"targets={len(packet.targets)}; summary={packet.summary[:120]}"
        )
        return output, message

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
                error=str(exc),
            )
            return

        output = self._build_output(payload, packet)
        yield self._sse_event(
            status="Completed",
            progress="100%",
            stage="done",
            role=self.role,
            work_item=work_item,
            workflow_id=payload.get("workflow_id"),
            command=command,
            message=(
                f"Tactical intelligence completed command={command}; "
                f"targets={len(packet.targets)}"
            ),
            output=output,
            intelligence_packet=packet.model_dump(mode="json"),
            output_attachments=packet.output_attachments,
            target_count=len(packet.targets),
        )
