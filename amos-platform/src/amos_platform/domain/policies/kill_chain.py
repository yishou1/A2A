"""F2T2EA kill-chain phase policy."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


KILL_CHAIN_PHASES = ["FIND", "FIX", "TRACK", "TARGET", "ENGAGE", "ASSESS"]
KILL_CHAIN_TIMEOUTS: dict[str, int] = {
    "FIND": 120,
    "FIX": 90,
    "TRACK": 180,
    "TARGET": 60,
    "ENGAGE": 30,
    "ASSESS": 60,
}
LEGACY_KILL_CHAIN_PHASE_ALIASES = {
    "DETECT": "FIND",
    "IDENTIFY": "FIX",
    "DECIDE": "TARGET",
}


def normalize_kill_chain_phase(
    phase: str,
    default: str | None = None,
) -> str | None:
    phase_upper = phase.upper()
    if phase_upper in KILL_CHAIN_PHASES:
        return phase_upper
    if phase_upper in LEGACY_KILL_CHAIN_PHASE_ALIASES:
        return LEGACY_KILL_CHAIN_PHASE_ALIASES[phase_upper]
    return default


def get_kill_chain_timeout(
    phase: str,
    default: int | None = None,
) -> int | None:
    normalized_phase = normalize_kill_chain_phase(phase)
    if normalized_phase is None:
        return default
    return KILL_CHAIN_TIMEOUTS.get(normalized_phase, default)


def advance_track_phase(
    current_phase: str,
    *,
    confidence: float = 0.0,
    classification: str = "UNKNOWN",
    threat_level: str = "UNKNOWN",
) -> str:
    """Return the next track phase using the existing fusion thresholds."""
    if current_phase == "FIND" and confidence > 0.4:
        return "FIX"
    if current_phase == "FIX" and confidence > 0.7 and classification != "UNKNOWN":
        return "TRACK"
    if current_phase == "TRACK" and threat_level in ("CRITICAL", "HIGH"):
        return "TARGET"
    return current_phase


def phase_counts(tracks: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Count track phases from serialized fused-track payloads."""
    counts = {phase: 0 for phase in KILL_CHAIN_PHASES}
    for track in tracks:
        kill_chain = track.get("kill_chain") if isinstance(track, Mapping) else None
        phase = kill_chain.get("phase") if isinstance(kill_chain, Mapping) else None
        if phase in counts:
            counts[phase] += 1
    return counts


def requires_decision_alert(track: Mapping[str, Any]) -> bool:
    kill_chain = track.get("kill_chain")
    phase = kill_chain.get("phase") if isinstance(kill_chain, Mapping) else None
    normalized_phase = normalize_kill_chain_phase(str(phase or ""), default=str(phase or ""))
    return track.get("threat_level") == "CRITICAL" and normalized_phase == "TARGET"


__all__ = [
    "KILL_CHAIN_PHASES",
    "KILL_CHAIN_TIMEOUTS",
    "LEGACY_KILL_CHAIN_PHASE_ALIASES",
    "advance_track_phase",
    "get_kill_chain_timeout",
    "normalize_kill_chain_phase",
    "phase_counts",
    "requires_decision_alert",
]
