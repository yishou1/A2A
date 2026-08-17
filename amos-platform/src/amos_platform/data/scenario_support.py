"""Small operator-display catalog for the active demonstration scenario.

This module intentionally contains only data required to draw the current
scenario.  Large downloaded catalogs and offline database caches are not part
of the AMOS runtime contract.
"""

from __future__ import annotations

from typing import Any


SENSOR_MODELS: dict[str, dict[str, Any]] = {
    "AESA_RADAR": {"sensor_type": "radar", "range_nm": 40, "fov_deg": 360},
    "RADAR": {"sensor_type": "radar", "range_nm": 22, "fov_deg": 120},
    "SAR": {"sensor_type": "sar", "range_nm": 30, "fov_deg": 55},
    "EO/IR": {"sensor_type": "eo_ir", "range_nm": 12, "fov_deg": 35},
    "EO_IR": {"sensor_type": "eo_ir", "range_nm": 12, "fov_deg": 35},
    "SIGINT": {"sensor_type": "sigint", "range_nm": 30, "fov_deg": 360},
    "ELINT": {"sensor_type": "elint", "range_nm": 40, "fov_deg": 360},
    "COMINT": {"sensor_type": "comint", "range_nm": 25, "fov_deg": 360},
    "AIS": {"sensor_type": "ais", "range_nm": 25, "fov_deg": 360},
}


def get_scenario_support() -> dict[str, Any]:
    """Return immutable-in-practice display support as a fresh mapping."""
    return {
        "sensor_models": {name: dict(spec) for name, spec in SENSOR_MODELS.items()},
    }


__all__ = ["SENSOR_MODELS", "get_scenario_support"]
