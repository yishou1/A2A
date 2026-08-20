"""Runtime mode for the single-process AMOS simulation host."""

from __future__ import annotations

import os
from enum import Enum


class PlatformMode(str, Enum):
    """Supported platform operating modes."""

    OFFLINE = "offline"
    CONNECTED = "connected"
    DEBUG = "debug"


def read_platform_mode() -> PlatformMode:
    """Read the platform mode from the environment."""
    raw = os.environ.get("AMOS_PLATFORM_MODE", "").strip().lower()
    if not raw:
        raw = "debug" if os.environ.get("AMOS_ENABLE_TRUTH_DEBUG", "").lower() == "true" else "offline"
    try:
        return PlatformMode(raw)
    except ValueError:
        return PlatformMode.OFFLINE


__all__ = ["PlatformMode", "read_platform_mode"]
