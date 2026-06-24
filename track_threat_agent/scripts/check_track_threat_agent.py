"""Check Track Threat Agent health, readiness, card, and Nacos-facing metadata."""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import URLError
from urllib.request import urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a running Track Threat Agent.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8102")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    try:
        health = _get_json(f"{base_url}/health")
        ready = _get_json(f"{base_url}/ready")
        card = _get_json(f"{base_url}/.well-known/agent-card")
    except URLError as exc:
        print(f"check failed: {exc}", file=sys.stderr)
        return 1

    result = {
        "base_url": base_url,
        "health": {
            "status": health.get("status"),
            "ready": health.get("ready"),
            "agent_status": health.get("agent_status"),
            "active_track_count": health.get("active_track_count"),
            "nacos": health.get("nacos"),
        },
        "ready": ready,
        "agent_card": {
            "name": card.get("name"),
            "role": card.get("role"),
            "capabilities": card.get("capabilities", []),
            "sendMessageEndpoint": card.get("sendMessageEndpoint"),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if health.get("status") != "ok" or not ready.get("ready", False):
        return 2
    return 0


def _get_json(url: str) -> dict:
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
