#!/usr/bin/env python3
"""Regenerate static SVG previews from deterministic scenario replay data.

Only derived simulation products are exported. Command products remain bound
to verified backend results at runtime and are intentionally not fabricated.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from amos_platform.data.scenario_repository import get_scenario
from amos_platform.media.evidence_products import render_evidence_product
from amos_platform.simulation.engine import SimEngine


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "maritime-convoy-air-defense": "standard",
}


def replay(scenario_id: str, branch: str) -> tuple[dict, SimEngine]:
    scenario = deepcopy(get_scenario(scenario_id))
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine.clock.update({
        "run_id": f"preview-{scenario_id}",
        "scenario_id": scenario_id,
        "scenario_branch": branch,
        "director_mode": "integration",
    })
    engine.evaluate_media_captures()
    due = [
        plan for plan in scenario["capture_plans"]
        if "*" in set(plan.get("branch_ids") or ["*"]) or branch in set(plan.get("branch_ids") or [])
    ]
    end = max(float(plan["at_sec"]) for plan in due if plan["product_type"] != "command_product")
    while float(engine.clock["elapsed_sec"]) < end:
        elapsed = float(engine.clock["elapsed_sec"])
        step = min(30.0, end - elapsed)
        engine._tick(step)
    return scenario, engine


def main() -> None:
    checksums: dict[str, str] = {}
    for scenario_id, branch in SCENARIOS.items():
        scenario, engine = replay(scenario_id, branch)
        media_by_id = {str(item["media_id"]): item for item in scenario["media_cues"]}
        for capture in engine.media_capture.public_captures():
            if capture.get("product_type") != "derived_sensor_product":
                continue
            if capture.get("mime_type") != "image/svg+xml":
                continue
            media = media_by_id[str(capture["media_id"])]
            rendered = render_evidence_product(
                capture,
                {"clock": {"run_id": capture.get("run_id"), "elapsed_sec": capture.get("captured_at_sim_time")}},
            )
            relative = str(media["uri"]).removeprefix("/static/")
            destination = ROOT / "static" / relative
            destination.write_bytes(rendered["bytes"])
            checksums[destination.name] = rendered["checksum"]["value"]
    print(json.dumps(checksums, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
