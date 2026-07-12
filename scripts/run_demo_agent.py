from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a2a_protocol.server import A2ABaseAgent  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a demo A2A Agent with crowd worker enabled.")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--claim-interval", type=float, default=0.1)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    agent = A2ABaseAgent(
        name=args.name,
        description=f"Demo {args.role} crowd worker process",
        role=args.role,
        port=args.port,
        agent_id=args.agent_id,
        crowd_worker_enabled=True,
        crowd_claim_interval=args.claim_interval,
    )
    agent.start()


if __name__ == "__main__":
    main()
