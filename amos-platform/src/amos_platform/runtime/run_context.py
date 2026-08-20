"""Current simulation run metadata."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid

from amos_platform.runtime.mode import PlatformMode


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(slots=True)
class RunContext:
    """Small metadata object for the active single-process run."""

    scenario_id: str = "amphibious-landing-joint-operation"
    run_id: str = field(default_factory=lambda: f"run-{uuid.uuid4().hex[:12]}")
    platform_mode: PlatformMode = PlatformMode.OFFLINE
    agent_backend: str = "gateway"
    seed: int = 12026
    started_at: str = field(default_factory=_now_iso)

    def reset(self, scenario_id: str | None = None, *, seed: int | None = None) -> None:
        """Start a new logical run while keeping the singleton runtime alive."""
        if scenario_id:
            self.scenario_id = scenario_id
        if seed is not None:
            self.seed = int(seed)
        self.run_id = f"run-{uuid.uuid4().hex[:12]}"
        self.started_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["platform_mode"] = self.platform_mode.value
        data["agent_backend"] = self.agent_backend
        return data
