"""Export the deterministic long landing-demo scenario to JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from app.scenario_generator import generate_long_operation_sequence


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Track Threat Agent long scenario frames.")
    parser.add_argument("--frames", type=int, default=90)
    parser.add_argument("--output", type=Path, default=Path("sample_data/coastal_operation_90_frames.json"))
    args = parser.parse_args()

    sequence = generate_long_operation_sequence(frame_count=args.frames)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(sequence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output} with {len(sequence['frames'])} frames")


if __name__ == "__main__":
    main()
