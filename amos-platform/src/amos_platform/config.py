"""Repository paths used by the AMOS web application."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def template_dir() -> Path:
    return REPO_ROOT / "templates"


def static_dir() -> Path:
    return REPO_ROOT / "static"


def tile_root() -> Path:
    return static_dir() / "tiles"


def tile_manifest_path() -> Path:
    return tile_root() / "manifest.json"


def run_database_path() -> Path:
    """Return the run archive database path.

    Relative ``AMOS_RUN_DB`` values are resolved from the repository root so
    service startup does not silently move the archive with the current
    working directory.
    """
    configured = os.getenv("AMOS_RUN_DB", "").strip()
    if not configured:
        return REPO_ROOT / "instance" / "amos_runs.sqlite3"
    path = Path(configured).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


__all__ = [
    "REPO_ROOT",
    "run_database_path",
    "static_dir",
    "template_dir",
    "tile_manifest_path",
    "tile_root",
]
