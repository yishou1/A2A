"""验证 A2A Commander 协议（sendMessage / sendMessageStream）。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TIA_CONFIG", "config/default.yaml")

from a2a_protocol.client import A2AClient
from workflow_payloads import build_attachment_ref


def demo_payload() -> dict:
    attachment = build_attachment_ref(
        "https://minio.example.local/a2a/recon/frame-001.jpg",
        sha256="abc123",
        kind="image",
        mime_type="image/jpeg",
        attachment_id="recon-frame-001",
        meta={"sensor_id": "EO-1", "modality": "eo_ir"},
    )
    return {
        "workflow_id": "workflow-verify-001",
        "work_item": "workflow-verify-001:activatity-002-processintelligence",
        "command": "process_intelligence",
        "input": {
            "recon_report": "Sector_A fortified with overlapping MG nests.",
            "sector": "Sector_A",
        },
        "context": {
            "jamming_level": 0.15,
            "subscriber_agents": ["commander", "artillery"],
        },
        "attachments": [attachment],
    }


def main() -> int:
    host = os.environ.get("TIA_HOST", "127.0.0.1")
    port = int(os.environ.get("TIA_PORT", "8015"))
    client = A2AClient(host, port)

    card = client.discover()
    print(f"[OK] Agent Card: {card['name']} role={card['role']}")
    assert card.get("sendMessageEndpoint") == "/sendMessage"

    token = client.authenticate()
    print(f"[OK] authenticate -> token={token[:12]}...")

    payload = demo_payload()
    ack = client.send_message(payload)
    print(f"[OK] sendMessage -> status={ack.get('status')} role={ack.get('role')}")
    print(f"     message={ack.get('message')}")
    assert ack.get("role") == "tactical_intelligence"
    assert ack.get("status") in {"Accepted", "Failed"}

    events = list(client.send_message_stream(payload))
    print(f"[OK] sendMessageStream -> {len(events)} events")
    last = json.loads(events[-1])
    assert last.get("status") == "Completed"
    packet = last.get("intelligence_packet") or {}
    print(f"     targets={len(packet.get('targets', []))}")

    ack_repeat = client.send_message(payload)
    assert ack_repeat.get("work_item") == ack.get("work_item")
    print("[OK] idempotent sendMessage (same work_item)")

    print("\n[PASS] Commander A2A interface verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
